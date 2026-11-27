"""Streaming transcription on the server while the user is still speaking.

  audio + VAD probs ──► Segmenter ──(cut at a natural pause)──► transcribe that phrase now

On key release only the last open phrase is left to transcribe, so the release-to-text time
doesn't grow with the length of the dictation. Phrases partition the audio exactly (each cut is
the start of the next phrase): nothing is dropped or duplicated. Each phrase is VAD-trimmed
(with padding) before ASR. One ASR worker thread runs jobs by priority: the final phrase first.
"""

from __future__ import annotations

import itertools
import logging
import queue
import threading
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass, field

import numpy as np

from dictate.asr.base import ASRBackend
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


# -- ASR worker ----------------------------------------------------------------------------------
FINAL, COMMIT = 0, 1


@dataclass(order=True)
class _Job:
    priority: int
    seq: int
    fn: Callable[[], str] = field(compare=False)
    future: Future = field(compare=False)


class ASRWorker:
    """Single thread that owns the ASR model; runs jobs by priority (final phrases first)."""

    def __init__(self, asr: ASRBackend, lock: threading.Lock) -> None:
        self.asr = asr
        self.lock = lock  # shared with KeepWarm, which never blocks on it
        self._q: queue.PriorityQueue[_Job] = queue.PriorityQueue()
        self._seq = itertools.count()
        threading.Thread(target=self._run, name="asr-worker", daemon=True).start()

    def submit(self, priority: int, fn: Callable[[], str]) -> Future:
        fut: Future = Future()
        self._q.put(_Job(priority, next(self._seq), fn, fut))
        return fut

    def transcribe(self, audio: FloatArray, **kwargs: object) -> str:
        """`kwargs` (language, vocabulary) go to backends that take them (the online ones)."""
        return self.asr.transcribe(audio, **kwargs).text  # type: ignore[call-arg]

    def _run(self) -> None:
        while True:
            job = self._q.get()
            try:
                with self.lock:
                    job.future.set_result(job.fn())
            except Exception as e:  # never kill the worker
                log.exception("ASR job failed")
                job.future.set_exception(e)


# -- one utterance ---------------------------------------------------------------------------------
@dataclass
class StreamConfig:
    threshold: float = 0.5
    pause_ms: float = 450
    min_segment_s: float = 1.5
    max_segment_s: float = 20.0
    pad_ms: float = 250
    max_pause_ms: float = 700
    min_speech_ms: float = 150


class StreamingSession:
    """Fed (in order) with 512-sample float32 blocks and their VAD probabilities."""

    def __init__(
        self,
        worker: ASRWorker,
        cfg: StreamConfig,
        *,
        on_commit: Callable[[int, str], None] | None = None,
        asr_kwargs: dict | None = None,
    ) -> None:
        self.worker = worker
        self.cfg = cfg
        self.asr_kwargs = asr_kwargs or {}
        self.on_commit = on_commit
        self.segmenter = Segmenter(
            threshold=cfg.threshold, pause_ms=cfg.pause_ms, min_segment_s=cfg.min_segment_s,
            max_segment_s=cfg.max_segment_s,
        )  # fmt: skip
        self.segments = 0
        # Only the open phrase's audio is kept (everything before the last cut is already transcribed or
        # queued), so a 30-minute dictation needs no more memory than a short one. The buffer holds the
        # samples [_offset, _n) of the whole recording.
        self._buf = np.zeros(SAMPLE_RATE * 30, dtype=np.float32)  # grows by doubling if a phrase is that long
        self._n = 0  # samples received so far
        self._offset = 0  # samples dropped from the front
        self._cuts: list[int] = [0]
        self._futures: list[Future] = []
        self._finalized = False

    def feed(self, block: FloatArray, probs: list[float]) -> None:
        if self._finalized:
            return
        held = self._n - self._offset
        if held + len(block) > len(self._buf):
            grown = np.zeros(max(len(self._buf) * 2, held + len(block)), dtype=np.float32)
            grown[:held] = self._buf[:held]
            self._buf = grown
        self._buf[held : held + len(block)] = block
        self._n += len(block)
        for p in probs:
            cut = self.segmenter.push(p)
            if cut is not None:
                self._commit_until(cut)

    def _audio(self, start_frame: int, end_frame: int | None) -> tuple[FloatArray, list[float]]:
        start = max(start_frame * FRAME, self._offset)
        end = self._n if end_frame is None else min(self._n, end_frame * FRAME)
        audio = self._buf[start - self._offset : max(start, end) - self._offset].copy()
        return audio, self.segmenter.probs[start_frame:end_frame]

    def _forget_until(self, frame: int) -> None:
        """Audio before `frame` won't be needed again: drop it."""
        cut = min(frame * FRAME, self._n)
        if cut <= self._offset:
            return
        keep = self._n - cut
        self._buf[:keep] = self._buf[cut - self._offset : self._n - self._offset]
        self._offset = cut

    def _transcribe_segment(self, audio: FloatArray, probs: list[float]) -> str:
        c = self.cfg
        t = trim(audio, probs, threshold=c.threshold, pad_ms=c.pad_ms, max_pause_ms=c.max_pause_ms,
                 min_speech_ms=c.min_speech_ms)  # fmt: skip
        return "" if t is None else self.worker.transcribe(t.audio, **self.asr_kwargs)

    def _commit_until(self, cut: int) -> None:
        index = len(self._futures)
        start = self._cuts[-1]
        self._cuts.append(cut)
        audio, probs = self._audio(start, cut)
        self._forget_until(cut)  # (the phrase has its own copy now)

        def run() -> str:
            text = self._transcribe_segment(audio, probs)
            if self.on_commit:
                self.on_commit(index, text)
            return text

        self._futures.append(self.worker.submit(COMMIT, run))
        self.segments += 1

    def cancel(self) -> None:
        self._finalized = True

    def finalize(self, timeout_s: float = 15.0) -> list[str]:
        """Transcribe the last open phrase and return every phrase's text, in order."""
        self._finalized = True
        audio, probs = self._audio(self._cuts[-1], None)
        final = self.worker.submit(FINAL, lambda: self._transcribe_segment(audio, probs))
        texts = [f.result(timeout=timeout_s) for f in self._futures]
        texts.append(final.result(timeout=timeout_s))
        return texts

    @property
    def audio_s(self) -> float:
        return self._n / SAMPLE_RATE
