"""Streaming transcription while the user is still speaking.

  audio blocks + VAD probs ──► Segmenter ──(cut at a pause)──► COMMIT job: transcribe that segment
                     │
                     └─(every ~400 ms)──► PREVIEW job: transcribe the open segment ──► Stabilizer ──► overlay

On key release only the last open segment is left (FINAL job, highest priority), so the
release-to-text time no longer grows with the length of the dictation.

Segments partition the timeline exactly (each cut is the start of the next segment), so no
audio is dropped or duplicated; each segment is VAD-trimmed (with padding) before ASR.

Priority rule: one ASR worker thread runs jobs in order FINAL > COMMIT > PREVIEW. Previews
are skipped if one is already queued, dropped once their segment is committed, and never run
after release.
"""

from __future__ import annotations

import difflib
import itertools
import logging
import queue
import re
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass, field

import numpy as np

from dictate.asr.base import ASRBackend
from dictate.cleanup.formatter import remove_fillers, tidy_spacing
from dictate.vad import FRAME, FRAME_MS, SAMPLE_RATE, FloatArray, trim

log = logging.getLogger(__name__)


# -- segmentation ------------------------------------------------------------------------------
class Segmenter:
    """Decides where to cut the incoming audio, one VAD frame at a time (pure logic)."""

    def __init__(
        self,
        *,
        threshold: float = 0.5,
        pause_ms: float = 450,
        min_segment_s: float = 1.5,
        max_segment_s: float = 20.0,
    ) -> None:
        self.threshold = threshold
        self.neg = threshold - 0.15
        self.pause_frames = max(2, round(pause_ms / FRAME_MS))
        self.min_frames = round(min_segment_s * 1000 / FRAME_MS)
        self.max_frames = round(max_segment_s * 1000 / FRAME_MS)
        self.probs: list[float] = []
        self.seg_start = 0  # frame index where the open segment starts
        self._in_speech = False
        self._had_speech = False
        self._silence_run = 0

    def push(self, prob: float) -> int | None:
        """Add one frame's speech probability. Returns a cut frame index when a segment completes."""
        self.probs.append(prob)
        n = len(self.probs)
        self._in_speech = prob >= (self.neg if self._in_speech else self.threshold)
        if self._in_speech:
            self._had_speech = True
            self._silence_run = 0
        else:
            self._silence_run += 1

        length = n - self.seg_start
        if self._had_speech and self._silence_run >= self.pause_frames and length >= self.min_frames:
            return self._cut(n - self._silence_run // 2)
        if length >= self.max_frames:
            lo = self.seg_start + (length * 2) // 3
            window = self.probs[lo:n]
            return self._cut(lo + int(np.argmin(window)) if window else n)
        return None

    def _cut(self, frame: int) -> int:
        self.seg_start = frame
        self._had_speech = self._in_speech or any(p >= self.threshold for p in self.probs[frame:])
        self._silence_run = 0 if self._in_speech else self._silence_run
        return frame

    @property
    def open_has_speech(self) -> bool:
        return self._had_speech


# -- preview stabilization (LocalAgreement-2) ----------------------------------------------------
def display_text(text: str, language: str = "en") -> str:
    """Preview-only: hide filler sounds (cleanup removes them anyway), so they never flicker away."""
    out = tidy_spacing(remove_fillers(text, language), language)
    return out[:1].upper() + out[1:] if out else out


def _norm(word: str) -> str:
    return re.sub(r"[^\w']", "", word.lower())


class Stabilizer:
    """Words become *stable* once two consecutive hypotheses agree on them. Stable words are
    never taken back while the segment is open, so the preview doesn't flicker."""

    def __init__(self) -> None:
        self.stable: list[str] = []
        self._prev: list[str] | None = None

    def update(self, hypothesis: str) -> tuple[list[str], list[str]]:
        words = hypothesis.split()
        if self._prev is not None:
            agree = 0
            for a, b in zip(self._prev, words, strict=False):
                if _norm(a) != _norm(b):
                    break
                agree += 1
            consistent = all(_norm(s) == _norm(w) for s, w in zip(self.stable, words, strict=False))
            if consistent and agree > len(self.stable):
                self.stable = words[:agree]
        self._prev = words
        k = len(self.stable)
        unstable = words[k:] if all(_norm(s) == _norm(w) for s, w in zip(self.stable, words, strict=False)) else []
        return list(self.stable), unstable

    def reset(self) -> None:
        self.stable, self._prev = [], None


# -- ASR worker ----------------------------------------------------------------------------------
FINAL, COMMIT, PREVIEW = 0, 1, 2


@dataclass(order=True)
class _Job:
    priority: int
    seq: int
    fn: Callable[[], str] = field(compare=False)
    future: Future = field(compare=False)
    still_wanted: Callable[[], bool] = field(compare=False, default=lambda: True)


class ASRWorker:
    """Single thread that owns the ASR model; runs jobs by priority."""

    def __init__(self, asr: ASRBackend, lock: threading.Lock) -> None:
        self.asr = asr
        self.lock = lock  # shared with KeepWarm, which never blocks on it
        self._q: queue.PriorityQueue[_Job] = queue.PriorityQueue()
        self._seq = itertools.count()
        self._thread = threading.Thread(target=self._run, name="asr-worker", daemon=True)
        self._thread.start()

    def submit(self, priority: int, fn: Callable[[], str], still_wanted: Callable[[], bool] | None = None) -> Future:
        fut: Future = Future()
        self._q.put(_Job(priority, next(self._seq), fn, fut, still_wanted or (lambda: True)))
        return fut

    def transcribe(self, audio: FloatArray) -> str:
        return self.asr.transcribe(audio).text

    def _run(self) -> None:
        while True:
            job = self._q.get()
            if not job.still_wanted():
                job.future.cancel()
                continue
            try:
                with self.lock:
                    job.future.set_result(job.fn())
            except Exception as e:  # never kill the worker
                log.exception("ASR job failed")
                job.future.set_exception(e)


# -- one streaming utterance ----------------------------------------------------------------------
PreviewCallback = Callable[[str, list[str], list[str]], None]  # committed text, stable words, unstable words
CommitCallback = Callable[[int, str], None]  # segment index, text


@dataclass
class StreamStats:
    segments: int = 0
    previews: int = 0
    previews_skipped: int = 0
    revised_words: int = 0  # stable preview words that the committed text changed (flicker)
    stable_words: int = 0
    moved_words: int = 0  # stable words that turned out to belong to the next segment (not revised)


@dataclass
class StreamConfig:
    threshold: float = 0.5
    pause_ms: float = 450
    min_segment_s: float = 1.5
    max_segment_s: float = 20.0
    pad_ms: float = 250
    max_pause_ms: float = 700
    min_speech_ms: float = 150
    preview: bool = True
    preview_interval_ms: float = 400
    preview_min_speech_s: float = 0.4
    language: str = "en"


class StreamingSession:
    """Fed from the audio/VAD worker thread with (block, prob) pairs."""

    def __init__(
        self,
        worker: ASRWorker,
        cfg: StreamConfig,
        *,
        on_preview: PreviewCallback | None = None,
        on_commit: CommitCallback | None = None,
    ) -> None:
        self.worker = worker
        self.cfg = cfg
        self.on_preview = on_preview
        self.on_commit = on_commit
        self.segmenter = Segmenter(
            threshold=cfg.threshold, pause_ms=cfg.pause_ms, min_segment_s=cfg.min_segment_s,
            max_segment_s=cfg.max_segment_s,
        )  # fmt: skip
        self.stabilizer = Stabilizer()
        self.stats = StreamStats()
        self._buf = np.zeros(SAMPLE_RATE * 30, dtype=np.float32)  # grows by doubling
        self._n = 0
        self._cuts: list[int] = [0]  # frame indices where segments start
        self._futures: list[Future] = []  # commit futures, in segment order
        self._texts: dict[int, str] = {}
        self._lock = threading.Lock()
        self._finalized = False
        self._preview_pending = False
        self._last_preview = 0.0
        self._open_index = 0

    # -- feeding (vad-worker thread) --------------------------------------------------------------
    def feed(self, block: FloatArray, probs: list[float]) -> None:
        if self._finalized:
            return
        if self._n + len(block) > len(self._buf):
            grown = np.zeros(max(len(self._buf) * 2, self._n + len(block)), dtype=np.float32)
            grown[: self._n] = self._buf[: self._n]
            self._buf = grown
        self._buf[self._n : self._n + len(block)] = block
        self._n += len(block)
        for p in probs:
            cut = self.segmenter.push(p)
            if cut is not None:
                self._commit_until(cut)
        self._maybe_preview()

    def _audio(self, start_frame: int, end_frame: int | None) -> tuple[FloatArray, list[float]]:
        end = self._n if end_frame is None else min(self._n, end_frame * FRAME)
        probs = self.segmenter.probs[start_frame:end_frame]
        return self._buf[start_frame * FRAME : end].copy(), probs

    def _transcribe_segment(self, audio: FloatArray, probs: list[float]) -> str:
        c = self.cfg
        t = trim(audio, probs, threshold=c.threshold, pad_ms=c.pad_ms, max_pause_ms=c.max_pause_ms,
                 min_speech_ms=c.min_speech_ms)  # fmt: skip
        return "" if t is None else self.worker.transcribe(t.audio)

    def _commit_until(self, cut: int) -> None:
        index = len(self._futures)
        start = self._cuts[-1]
        self._cuts.append(cut)
        audio, probs = self._audio(start, cut)
        with self._lock:  # a late preview of the old segment must never touch the new one
            self._open_index = index + 1
            stable_now = list(self.stabilizer.stable)
            self.stabilizer = Stabilizer()

        def run() -> str:
            text = self._transcribe_segment(audio, probs)
            with self._lock:
                self._texts[index] = text
                leftover = self._note_revisions(stable_now, text)
                fresh = not self.stabilizer.stable and self._open_index == index + 1
            if self.on_commit:
                self.on_commit(index, text)
            # Words the preview showed that landed in the next segment stay visible (dimmed)
            # until that segment's first preview, instead of blinking out.
            self._emit_preview([], leftover if fresh else [])
            return text

        self._futures.append(self.worker.submit(COMMIT, run))
        self.stats.segments += 1

    def _note_revisions(self, stable: list[str], committed: str) -> list[str]:
        """Count stable preview words the committed text changed (aligned, so a dropped filler
        doesn't count every later word). Returns stable words past the end of the committed text."""
        a = [_norm(w) for w in stable]
        b = [_norm(w) for w in committed.split()]
        blocks = [bl for bl in difflib.SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks() if bl.size]
        matched = sum(bl.size for bl in blocks)
        leftover: list[str] = []
        if blocks:
            last = blocks[-1]
            if last.b + last.size == len(b):  # committed text ended; stable text kept going
                leftover = stable[last.a + last.size :]
        self.stats.stable_words += len(stable)
        self.stats.revised_words += len(stable) - matched - len(leftover)
        self.stats.moved_words += len(leftover)
        return leftover

    def committed_text(self) -> str:
        with self._lock:
            return " ".join(self._texts[i] for i in sorted(self._texts) if self._texts[i])

    def _emit_preview(self, stable: list[str], unstable: list[str]) -> None:
        if self.on_preview and not self._finalized:
            try:
                self.on_preview(display_text(self.committed_text(), self.cfg.language), stable, unstable)
            except Exception:
                log.debug("preview callback failed", exc_info=True)

    def _maybe_preview(self) -> None:
        c = self.cfg
        if not c.preview or self.on_preview is None or self._finalized:
            return
        now = time.perf_counter()
        if now - self._last_preview < c.preview_interval_ms / 1000:
            return
        if self._preview_pending:
            self.stats.previews_skipped += 1  # the previous preview hasn't finished: skip this round
            self._last_preview = now
            return
        start = self._cuts[-1]
        open_frames = len(self.segmenter.probs) - start
        if not self.segmenter.open_has_speech or open_frames * FRAME_MS < c.preview_min_speech_s * 1000:
            return
        audio, probs = self._audio(start, None)
        with self._lock:
            index = self._open_index
            stabilizer = self.stabilizer
        self._last_preview = now
        self._preview_pending = True

        def run() -> str:
            try:
                text = self._transcribe_segment(audio, probs)
                with self._lock:
                    current = index == self._open_index and not self._finalized
                    if current:
                        stable, unstable = stabilizer.update(display_text(text, self.cfg.language))
                        self.stats.previews += 1
                if current:
                    self._emit_preview(stable, unstable)
                return text
            finally:
                self._preview_pending = False

        def wanted() -> bool:
            ok = index == self._open_index and not self._finalized
            if not ok:
                self._preview_pending = False
            return ok

        self.worker.submit(PREVIEW, run, still_wanted=wanted)

    def cancel(self) -> None:
        """Discard: stop previews and ignore any further audio."""
        self._finalized = True

    # -- release (processor thread) -------------------------------------------------------------
    def finalize(self, timeout_s: float = 10.0) -> list[str]:
        """Transcribe the last open segment and return every segment's text, in order."""
        self._finalized = True
        start = self._cuts[-1]
        audio, probs = self._audio(start, None)
        final = self.worker.submit(FINAL, lambda: self._transcribe_segment(audio, probs))
        texts = [f.result(timeout=timeout_s) for f in self._futures]
        texts.append(final.result(timeout=timeout_s))
        return texts

    @property
    def audio_s(self) -> float:
        return self._n / SAMPLE_RATE
