"""Daemon orchestrator: idle -> recording -> processing -> idle.

Threads:
  main        - Qt event loop for the preview overlay (or an idle loop when the preview is off)
  hotkeys     - pynput hook (enqueue only) + gesture state machine -> App.on_event
  audio       - PortAudio callback (copy only) -> vad-worker (VAD, then the streaming session)
  asr-worker  - all ASR, by priority: final segment > committed segments > previews
  incremental - cleans finished sentences while you speak (one LLM call at a time)
  processor   - one utterance at a time: finish last segment -> clean tail -> insert
Nothing on the hotkey or audio threads ever waits on a model.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import Any

from dictate import models, platform_win
from dictate.asr import create_asr
from dictate.asr.keepwarm import KeepWarm
from dictate.audio import Recorder, level_report
from dictate.cleanup.incremental import IncrementalCleaner
from dictate.cleanup.llm_cleanup import Cleaner
from dictate.config import Config, ConfigWatcher
from dictate.hotkeys import Action, Event, GestureRecognizer, HotkeyListener
from dictate.insert import TextInserter
from dictate.llm import LLMBackend, LLMError, create_llm
from dictate.pipeline import Pipeline
from dictate.sounds import Sounds
from dictate.streaming import ASRWorker, StreamConfig, StreamingSession
from dictate.timing import Timings
from dictate.vad import SileroVAD

log = logging.getLogger("dictate")


@dataclass
class _Job:
    future: Any
    raw: bool
    t_release: float
    session: StreamingSession | None = None
    incremental: IncrementalCleaner | None = None


@dataclass
class _LastInsert:
    hwnd: int
    method: str
    at: float


class App:
    def __init__(self, cfg: Config, *, dry_run: bool = False) -> None:
        self.cfg = cfg
        self.dry_run = dry_run  # print results instead of inserting them (mic / setup check)
        self.watcher = ConfigWatcher(cfg)
        missing = models.missing_models(cfg)
        if missing:
            raise SystemExit(f"Missing models: {', '.join(missing)}. Run `dictate setup` first.")

        t0 = time.perf_counter()
        self.vad = SileroVAD(models.silero_path(cfg))
        self.vad.warmup()
        log.info("loading ASR %s on %s ...", cfg.asr.model, cfg.asr.device)
        self.asr = create_asr(cfg)
        self.asr.warmup()
        self.llm: LLMBackend | None = None
        cleaner: Cleaner | None = None
        if cfg.cleanup.enabled:
            log.info("starting cleanup model %s (%s) ...", cfg.cleanup.model, cfg.cleanup.backend)
            try:
                self.llm = create_llm(cfg)
                cleaner = Cleaner(self.llm, cfg)
                cleaner.warm()
            except LLMError as e:  # fail soft: dictation still works with rule-based formatting
                log.error("cleanup model unavailable, using rule-based formatting only: %s", e)
                self.llm, cleaner = None, None
        log.info("models ready in %.1fs (%s)", time.perf_counter() - t0, self.asr.name)

        self.pipeline = Pipeline(cfg, self.asr, cleaner)
        self.asr_lock = threading.Lock()
        self.asr_worker = ASRWorker(self.asr, self.asr_lock) if cfg.streaming.enabled else None
        # Live previews keep the GPU busy (and clocked up) while you speak; otherwise use keep-warm.
        previews_on = cfg.streaming.enabled and cfg.preview.enabled
        self.keep_warm: KeepWarm | None = None
        if cfg.asr.keep_warm and getattr(self.asr, "device", "cpu") == "cuda" and not previews_on:
            self.keep_warm = KeepWarm(self.asr, self.asr_lock)
        self.overlay: Any = None  # OverlayBridge, created on the UI thread in run()
        self._session: StreamingSession | None = None
        self._incremental: IncrementalCleaner | None = None
        self.recorder = Recorder(
            self.vad,
            device=cfg.audio.input_device,
            preroll_ms=cfg.audio.preroll_ms,
            always_open=cfg.audio.always_open,
            max_record_s=cfg.audio.max_record_s,
        )
        self.inserter = TextInserter(
            method=cfg.insert.method,
            restore_clipboard=cfg.insert.restore_clipboard,
            restore_delay_ms=cfg.insert.restore_delay_ms,
            type_apps=cfg.insert.type_apps,
            newline_key=cfg.insert.newline_key,
        )
        from dictate import paths

        self.sounds = Sounds(paths.data_dir() / "sounds", enabled=cfg.feedback.sounds, volume=cfg.feedback.volume)
        self.recognizer = GestureRecognizer(
            tap_max_s=cfg.hotkeys.tap_max_ms / 1000,
            double_tap_window_s=cfg.hotkeys.double_tap_window_ms / 1000,
            hands_free=cfg.hotkeys.hands_free,
        )
        self.hotkeys = HotkeyListener(
            self.recognizer,
            self.on_event,
            ptt=cfg.hotkeys.push_to_talk,
            cancel=cfg.hotkeys.cancel,
            raw_modifier=cfg.hotkeys.raw_modifier,
        )
        self._jobs: queue.Queue[_Job | None] = queue.Queue()
        self._processor = threading.Thread(target=self._process_loop, name="processor", daemon=True)
        self._level_warned = False
        self._last_insert: _LastInsert | None = None

    # -- lifecycle ----------------------------------------------------------------------
    def run(self) -> None:
        qt_app = self._start_overlay() if self.cfg.preview.enabled else None
        self.recorder.start()
        self._processor.start()
        self.hotkeys.start()
        hk = self.cfg.hotkeys
        print()
        print(f"  Ready. Hold [{hk.push_to_talk}] and speak; release to insert.")
        if hk.hands_free:
            print(f"  Double-tap [{hk.push_to_talk}] for hands-free; tap it again to stop.")
        print(f"  [{hk.cancel}] cancels. Hold [{hk.raw_modifier}] while releasing for raw text.")
        print(f"  Language: {self.cfg.asr.language}  (change with `dictate lang es|fr|en`)")
        cleanup = f"{self.cfg.cleanup.model}" if self.pipeline.cleaner else "off (rule-based formatting)"
        print(f"  AI cleanup: {cleanup}")
        print(f"  Live preview: {'on' if self.overlay else 'off'}")
        print("  Ctrl+C here to quit.\n")
        try:
            if qt_app is not None:
                qt_app.exec()
            else:
                while True:
                    time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            print("\nbye")
            self.shutdown()

    def _start_overlay(self) -> Any:
        """Create the Qt app + overlay on this (main) thread. Returns the QApplication."""
        import signal

        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication

        from dictate.ui.overlay import OverlayBridge, PreviewOverlay

        qt_app = QApplication.instance() or QApplication([])
        qt_app.setQuitOnLastWindowClosed(False)
        self._overlay_widget = PreviewOverlay(self.cfg.preview)
        self.overlay = OverlayBridge(self._overlay_widget)
        signal.signal(signal.SIGINT, lambda *_: qt_app.quit())
        self._sigint_timer = QTimer()  # lets Python see Ctrl+C while Qt's loop is running
        self._sigint_timer.timeout.connect(lambda: None)
        self._sigint_timer.start(200)
        return qt_app

    def shutdown(self) -> None:
        self.hotkeys.stop()
        self._jobs.put(None)
        self.inserter.flush()
        self.recorder.close()
        if self.llm is not None:
            self.llm.close()

    # -- hotkey thread ------------------------------------------------------------------
    def _new_session(self) -> tuple[StreamingSession | None, IncrementalCleaner | None]:
        if self.asr_worker is None:
            return None, None
        c = self.cfg
        incremental = self.pipeline.new_incremental()
        session = StreamingSession(
            self.asr_worker,
            StreamConfig(
                threshold=c.vad.threshold, pause_ms=c.streaming.segment_pause_ms,
                min_segment_s=c.streaming.min_segment_s, max_segment_s=c.streaming.max_segment_s,
                pad_ms=c.vad.pad_ms, max_pause_ms=c.vad.max_pause_ms, min_speech_ms=c.vad.min_speech_ms,
                preview=self.overlay is not None, preview_interval_ms=c.preview.interval_ms, language=c.asr.language,
            ),
            on_preview=self.overlay.transcript if self.overlay is not None else None,
            on_commit=(lambda _i, text: incremental.commit(text)) if incremental else None,
        )  # fmt: skip
        return session, incremental

    def _drop_session(self) -> None:
        if self._session is not None:
            self._session.cancel()
        if self._incremental is not None:
            self._incremental.abandon()
        self._session, self._incremental = None, None
        if self.overlay is not None:
            self.overlay.hide()

    def on_event(self, ev: Event) -> None:
        if ev.action == Action.START:
            self._session, self._incremental = self._new_session()
            self.recorder.begin(listener=self._session.feed if self._session else None)
            if self.keep_warm:
                self.keep_warm.start()
            self.sounds.play("start")
            if self.overlay is not None:
                self.overlay.listening()
            threading.Thread(target=self.inserter.prefetch, daemon=True).start()
        elif ev.action == Action.HANDS_FREE:
            log.info("hands-free on (tap again to stop)")
            if self.overlay is not None:
                self.overlay.listening(hands_free=True)
        elif ev.action == Action.STOP:
            t_release = time.perf_counter()
            self.sounds.play("stop")
            fut = self.recorder.stop(self.cfg.audio.release_tail_ms)
            self._stop_keep_warm()
            if self.overlay is not None:
                cleaning = self.pipeline.llm_active and not ev.raw
                self.overlay.status("Cleaning…" if cleaning else "Transcribing…")
            self._jobs.put(_Job(fut, ev.raw, t_release, self._session, self._incremental))
            self._session, self._incremental = None, None
        elif ev.action == Action.CANCEL:
            self.recorder.cancel()
            self._stop_keep_warm()
            self._drop_session()
            self.sounds.play("cancel")
            log.info("cancelled")
        elif ev.action == Action.DISCARD:
            self.recorder.cancel()
            self._stop_keep_warm()
            self._drop_session()
            log.debug("discarded (%s)", ev.reason)

    # -- processor thread ---------------------------------------------------------------
    def _process_loop(self) -> None:
        while True:
            job = self._jobs.get()
            if job is None:
                return
            try:
                self._process(job)
            except Exception:  # fail soft: never crash the daemon on one utterance
                log.exception("utterance failed")
                self.sounds.play("error")
                if self.overlay is not None:
                    self.overlay.hide()

    def _process(self, job: _Job) -> None:
        if self.watcher.maybe_reload():
            c = self.cfg
            self.inserter.update(
                method=c.insert.method,
                restore_clipboard=c.insert.restore_clipboard,
                restore_delay_ms=c.insert.restore_delay_ms,
                type_apps=c.insert.type_apps,
                newline_key=c.insert.newline_key,
            )
            self.sounds.enabled = c.feedback.sounds

        t = Timings()
        t.start = job.t_release
        with t.stage("finalize"):
            rec = job.future.result(timeout=10)
            audio = rec.audio()

        if not self._level_warned and len(audio) > 0:
            warning = level_report(audio).warning()
            if warning:
                log.warning(warning)
                self._level_warned = True

        if job.session is not None:
            with t.stage("asr"):
                texts = job.session.finalize()
            if not any(x.strip() for x in texts):
                if job.incremental is not None:
                    job.incremental.abandon()
                res = None
            else:
                res = self.pipeline.finish_stream(texts, incremental=job.incremental, raw=job.raw, timings=t)
                res.audio_s = rec.duration_s
        else:
            with self.asr_lock:
                res = self.pipeline.process(audio, rec.probs, raw=job.raw, timings=t)
            if res.no_speech:
                res = None
        if res is None:
            log.info("no speech detected (%.1fs of audio) - nothing inserted", len(audio) / 16000)
            self.sounds.play("nothing")
            if self.overlay is not None:
                self.overlay.hide()
            return

        if res.scratch_previous:
            self._scratch_previous()
        if res.text and self.dry_run:
            print(f"  [dry run] {res.text!r}", flush=True)
        elif res.text:
            with t.stage("insert"):
                outcome = self.inserter.insert(res.text)
            fg = platform_win.foreground_info()
            self._last_insert = _LastInsert(fg.hwnd, outcome.method, time.perf_counter())
            if not outcome.ok:
                log.warning("insert: %s", outcome.note)
                self.sounds.play("error")
        elif not res.scratch_previous:
            log.info("transcript was empty after formatting")
        if self.overlay is not None:
            if res.text:
                self.overlay.result(res.text)
            else:
                self.overlay.hide()

        words = len(res.text.split())
        if job.raw:
            mode = " [raw]"
        elif res.fallback_reason:
            mode = f" [rule-based: {res.fallback_reason}]"
        elif res.used_llm:
            mode = " [cleaned]"
        else:
            mode = " [rules: already clean]" if res.skipped_llm else ""
        if res.locked_chunks:
            mode += f" [{res.locked_chunks} of {res.chunks} chunks cleaned while speaking]"
        log.info("%.1fs audio -> %d words%s | %s", res.audio_s, words, mode, t.summary())
        if self.cfg.logging.log_transcripts:
            log.info("  asr:   %s", res.asr_text)
            log.info("  final: %s", res.text.replace("\n", "\\n"))

    def _stop_keep_warm(self) -> None:
        if self.keep_warm:
            self.keep_warm.stop()

    def _scratch_previous(self) -> None:
        """'Scratch that' on its own: undo the previous paste (one Ctrl+Z) if it went into the
        same window within the last minute. Typed inserts can't be undone reliably."""
        last = self._last_insert
        fg = platform_win.foreground_info()
        if last and last.method == "paste" and last.hwnd == fg.hwnd and time.perf_counter() - last.at < 60:
            w = platform_win
            w.send_inputs(
                [
                    w.key_input(w.VK_CONTROL),
                    w.key_input(0x5A),
                    w.key_input(0x5A, flags=w.KEYEVENTF_KEYUP),
                    w.key_input(w.VK_CONTROL, flags=w.KEYEVENTF_KEYUP),
                ]
            )
            self._last_insert = None
            log.info("scratch that: undid previous insert")
        else:
            log.info("scratch that: nothing to undo here")
