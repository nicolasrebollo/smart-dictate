"""The speech engine (server side): models loaded once, one streaming session per dictation.

A session receives 16 kHz mono PCM16 audio while the user speaks. Speech detection, phrase
segmentation, transcription of finished phrases and AI cleanup of finished sentences all happen
as the audio arrives, so `finish()` only has the last phrase left to do.
"""

from __future__ import annotations

import copy
import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

from dictate import models
from dictate.cleanup.incremental import IncrementalCleaner, join_chunks
from dictate.cleanup.llm_cleanup import Cleaner
from dictate.config import LANGUAGES, Config
from dictate.pipeline import Pipeline
from dictate.streaming import ASRWorker, StreamConfig, StreamingSession
from dictate.timing import Timings
from dictate.vad import FRAME, SileroVAD

log = logging.getLogger(__name__)


@dataclass
class Result:
    text: str  # what to insert ("" = nothing was said; typing as you speak: what's left to type)
    heard: str  # raw speech recognition
    mode: str  # cleaned | rules | raw | fallback | empty
    ms: float  # time from "stop" to result, on the server
    audio_s: float


class Engine:
    def __init__(self, cfg: Config) -> None:
        from dictate.asr import create_asr
        from dictate.asr.keepwarm import KeepWarm
        from dictate.llm import LLMError, create_llm

        missing = models.missing_models(cfg)
        if missing:
            raise SystemExit(f"Missing models: {', '.join(missing)}. Run `dictate setup` first.")
        online = cfg.cleanup.backend == "cloud"
        self.cfg = cfg
        t0 = time.perf_counter()
        self._vads: queue.Queue[SileroVAD] = queue.Queue()
        for _ in range(2):
            v = SileroVAD(models.silero_path(cfg))
            v.warmup()
            self._vads.put(v)
        log.info("loading speech recognition (%s on %s)...", cfg.asr.model, cfg.asr.device)
        self.asr = create_asr(cfg)
        self.asr.warmup()
        self.llm = None
        if cfg.cleanup.enabled:
            log.info("starting AI cleanup model (%s)...", cfg.cleanup.model)
            try:
                self.llm = create_llm(cfg)
                if not online:  # warming fills a local model's cache; online it would only cost money
                    Cleaner(self.llm, cfg).warm()
            except (LLMError, RuntimeError) as e:  # fail soft: rule-based formatting still works
                log.error("AI cleanup unavailable, using rule-based formatting only: %s", e)
                self.llm = None
        self.lock = threading.Lock()
        self.worker = ASRWorker(self.asr, self.lock)
        on_gpu = getattr(self.asr, "device", "cpu") == "cuda"
        self._keep_warm = KeepWarm(self.asr, self.lock) if (cfg.asr.keep_warm and on_gpu) else None
        self._active = 0
        self._active_lock = threading.Lock()
        log.info("models ready in %.1fs", time.perf_counter() - t0)

    def _vad(self) -> SileroVAD:
        try:
            return self._vads.get_nowait()
        except queue.Empty:  # more simultaneous dictations than pooled VADs
            return SileroVAD(models.silero_path(self.cfg))

    def _activity(self, delta: int) -> None:
        # The GPU slows to a crawl after ~1 s idle: keep it clocked up while anyone is dictating.
        with self._active_lock:
            self._active += delta
            if self._keep_warm is not None:
                (self._keep_warm.start if self._active > 0 else self._keep_warm.stop)()

    def session(
        self, language: str = "en", source: str = "mic", on_text: Callable[[str], None] | None = None
    ) -> EngineSession:
        """source "computer": the audio is what's playing on the computer (a video, a call), not someone
        dictating, so spoken commands ("new paragraph", "scratch that", "comma") stay words.

        on_text: type as you speak. Each finished sentence is passed to it as soon as it's final (with
        the space or line break that goes before it), and finish() then returns only what's left."""
        return EngineSession(self, language if language in LANGUAGES else "en", source, on_text)

    def close(self) -> None:
        if self.llm is not None:
            self.llm.close()


class EngineSession:
    def __init__(
        self, engine: Engine, language: str, source: str = "mic", on_text: Callable[[str], None] | None = None
    ) -> None:
        self.engine = engine
        self.live = on_text is not None
        self._on_text = on_text
        self._typed: list[str] = []  # the chunks handed to on_text so far
        cfg = copy.deepcopy(engine.cfg)
        cfg.asr.language = language
        if source == "computer":
            cfg.commands.enabled = False
        cleaner = Cleaner(engine.llm, cfg) if engine.llm is not None else None
        self.pipeline = Pipeline(cfg, engine.asr, cleaner)
        self.incremental: IncrementalCleaner | None = self.pipeline.new_incremental(
            self._chunk_ready if self.live else None
        )
        inc = self.incremental
        self.stream = StreamingSession(
            engine.worker,
            StreamConfig(
                threshold=cfg.vad.threshold, pause_ms=cfg.streaming.segment_pause_ms,
                min_segment_s=cfg.streaming.min_segment_s, max_segment_s=cfg.streaming.max_segment_s,
                pad_ms=cfg.vad.pad_ms, max_pause_ms=cfg.vad.max_pause_ms, min_speech_ms=cfg.vad.min_speech_ms,
            ),
            on_commit=(lambda _i, text: inc.commit(text)) if inc is not None else None,
            asr_kwargs=(
                {"language": language, "vocabulary": tuple(cfg.vocabulary.words)}
                if getattr(engine.asr, "wants_language", False) else None
            ),
        )  # fmt: skip
        self.vad = engine._vad()
        self.vad.reset()
        self._pending = np.zeros(0, dtype=np.float32)
        self._done = False
        engine._activity(+1)

    def _chunk_ready(self, res: Any) -> None:
        """A sentence (or a few) is final: type it now, after what's already typed."""
        if self._done or self._on_text is None or not res.text:
            return
        before = join_chunks(self._typed)
        self._typed.append(res.text)
        piece = join_chunks(self._typed)[len(before) :]
        if piece.strip():
            self._on_text(piece)

    def feed_pcm16(self, data: bytes) -> None:
        """Audio as it's recorded: 16 kHz mono signed 16-bit little-endian."""
        if self._done or not data:
            return
        samples = np.frombuffer(data[: len(data) - len(data) % 2], dtype="<i2").astype(np.float32) / 32768.0
        buf = np.concatenate([self._pending, samples])
        n = len(buf) // FRAME * FRAME
        for i in range(0, n, FRAME):
            block = buf[i : i + FRAME]
            self.stream.feed(block, self.vad.feed(block))
        self._pending = buf[n:]

    def finish(self, raw: bool = False) -> Result:
        t = Timings()
        try:
            if len(self._pending):
                block = np.pad(self._pending, (0, FRAME - len(self._pending)))
                self.stream.feed(block, self.vad.feed(block))
            with t.stage("asr"):
                texts = self.stream.finalize()
            audio_s = self.stream.audio_s
            if not any(x.strip() for x in texts):
                if self.incremental is not None:
                    self.incremental.abandon()
                return Result("", "", "empty", t.total_ms(), audio_s)
            if self.live and self.incremental is not None:
                res, rest = self.pipeline.finish_live(texts, incremental=self.incremental, raw=raw, timings=t)
            else:
                res = self.pipeline.finish_stream(texts, incremental=self.incremental, raw=raw, timings=t)
                rest = res.text
            if raw:
                mode = "raw"
            elif res.fallback_reason:
                mode = "fallback"
            else:
                mode = "cleaned" if res.used_llm else "rules"
            log.info("%.1fs audio -> %d words [%s%s] | %s", audio_s, len(res.text.split()), mode,
                     f": {res.fallback_reason}" if res.fallback_reason else "", t.summary())  # fmt: skip
            if self.engine.cfg.logging.log_transcripts:
                log.info("  heard: %s\n  text:  %s", res.asr_text, res.text)
            return Result(rest, res.asr_text, mode, t.total_ms(), audio_s)
        finally:
            self._release()

    def cancel(self) -> None:
        self.stream.cancel()
        if self.incremental is not None:
            self.incremental.abandon()
        self._release()

    def _release(self) -> None:
        if not self._done:
            self._done = True
            self.engine._vads.put(self.vad)
            self.engine._activity(-1)
