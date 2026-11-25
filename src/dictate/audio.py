"""Microphone capture: pre-roll ring buffer, per-utterance recording, incremental VAD.

The PortAudio callback only copies blocks into a queue (never blocks, never runs models).
A worker thread appends blocks to the active recording and runs Silero VAD on them as they
arrive, so at key release the VAD is already ~done.
"""

from __future__ import annotations

import collections
import logging
import queue
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import sounddevice as sd

from dictate.vad import FRAME, SAMPLE_RATE, FloatArray, SileroVAD

log = logging.getLogger(__name__)

BLOCK = FRAME  # 32 ms blocks line up 1:1 with VAD frames


@dataclass
class Recording:
    blocks: list[FloatArray] = field(default_factory=list)
    probs: list[float] = field(default_factory=list)
    started_at: float = field(default_factory=time.perf_counter)

    def audio(self) -> FloatArray:
        if not self.blocks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(self.blocks)

    @property
    def duration_s(self) -> float:
        return sum(len(b) for b in self.blocks) / SAMPLE_RATE


@dataclass
class LevelReport:
    peak: float
    rms_dbfs: float
    clipped_fraction: float

    def warning(self) -> str | None:
        if self.clipped_fraction > 0.001:
            return "your microphone is clipping (too loud) - lower the input gain in Windows sound settings"
        # A quiet room is around -65 dBFS; a muted/blocked mic delivers (near-)digital silence.
        if self.rms_dbfs < -85:
            return (
                "the microphone signal is almost silent - check the input device, its volume, and "
                "Settings > Privacy & security > Microphone > 'Let desktop apps access your microphone'"
            )
        return None


def level_report(audio: FloatArray) -> LevelReport:
    if len(audio) == 0:
        return LevelReport(0.0, -120.0, 0.0)
    peak = float(np.max(np.abs(audio)))
    rms = float(np.sqrt(np.mean(np.square(audio, dtype=np.float64))))
    return LevelReport(
        peak=peak,
        rms_dbfs=20 * np.log10(max(rms, 1e-9)),
        clipped_fraction=float(np.mean(np.abs(audio) >= 0.99)),
    )


def resolve_device(spec: str | int) -> int | None:
    """'' -> default device; int -> index; str -> first input device whose name contains it."""
    if spec == "" or spec is None:
        return None
    if isinstance(spec, int):
        return spec
    if spec.isdigit():
        return int(spec)
    needle = spec.lower()
    for i, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] > 0 and needle in dev["name"].lower():
            return i
    raise ValueError(f"no input device matching {spec!r} (run `dictate devices`)")


def list_input_devices() -> list[tuple[int, str, str, bool]]:
    default_in = sd.default.device[0]
    hostapis = sd.query_hostapis()
    out = []
    for i, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] > 0:
            out.append((i, dev["name"], hostapis[dev["hostapi"]]["name"], i == default_in))
    return out


_FINISH = object()
Listener = Callable[[FloatArray, list[float]], None]


class Recorder:
    def __init__(
        self,
        vad: SileroVAD,
        *,
        device: str | int = "",
        preroll_ms: int = 300,
        always_open: bool = True,
        max_record_s: int = 600,
    ) -> None:
        self._vad = vad
        self._device = resolve_device(device)
        self._always_open = always_open
        self._max_blocks = int(max_record_s * SAMPLE_RATE / BLOCK)
        self._preroll: collections.deque[FloatArray] = collections.deque(
            maxlen=max(1, round(preroll_ms * SAMPLE_RATE / 1000 / BLOCK))
        )
        self._lock = threading.Lock()
        self._active = False
        self._queue: queue.Queue[Any] = queue.Queue()
        self._pending_stop: tuple[threading.Timer, Any] | None = None
        self._stream: sd.InputStream | None = None
        self._worker = threading.Thread(target=self._work, name="vad-worker", daemon=True)
        self._worker.start()

    # -- stream lifecycle ---------------------------------------------------------------
    def open(self) -> None:
        if self._stream is not None:
            return
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            blocksize=BLOCK,
            device=self._device,
            latency="low",
            callback=self._callback,
        )
        self._stream.start()
        info = sd.query_devices(self._stream.device)
        log.info("microphone: %s", info["name"])

    def close(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def start(self) -> None:
        if self._always_open:
            self.open()

    # -- audio callback (PortAudio thread: must never block) ----------------------------
    def _callback(self, indata: np.ndarray, frames: int, time_info: Any, status: sd.CallbackFlags) -> None:
        if status.input_overflow:
            log.debug("audio input overflow")
        block = indata[:, 0].copy()
        with self._lock:
            if self._active:
                self._queue.put(block)
            else:
                self._preroll.append(block)

    # -- recording control (called from the controller thread; all fast) ----------------
    @property
    def active(self) -> bool:
        return self._active

    def begin(self, listener: Listener | None = None) -> None:
        """Start a recording. `listener(block, probs)` gets every block (pre-roll first), in
        order, from the VAD worker thread - used by the streaming transcriber."""
        self._run_pending_stop()
        if not self._always_open:
            self.open()
        with self._lock:
            self._queue.put(("begin", list(self._preroll), listener))
            self._preroll.clear()
            self._active = True

    def stop(self, tail_ms: int = 0) -> Future[Recording]:
        """Stop recording after `tail_ms` (non-blocking). The future resolves to the audio + VAD
        probabilities once the worker has processed the last block."""
        fut: Future[Recording] = Future()
        done = threading.Event()

        def do_stop() -> None:
            if done.is_set():
                return
            done.set()
            with self._lock:
                self._active = False
                self._queue.put((_FINISH, fut))
                self._pending_stop = None
            if not self._always_open:
                self.close()

        if tail_ms <= 0:
            do_stop()
        else:
            timer = threading.Timer(tail_ms / 1000, do_stop)
            timer.daemon = True
            self._pending_stop = (timer, do_stop)
            timer.start()
        return fut

    def _run_pending_stop(self) -> None:
        pending = self._pending_stop
        if pending is not None:
            pending[0].cancel()
            pending[1]()

    def cancel(self) -> None:
        self._run_pending_stop()
        with self._lock:
            self._active = False
            self._queue.put(("cancel",))
        if not self._always_open:
            self.close()

    # -- worker -------------------------------------------------------------------------
    def _work(self) -> None:
        rec: Recording | None = None
        listener: Listener | None = None

        def add(block: FloatArray) -> None:
            assert rec is not None
            probs = self._vad.feed(block)
            rec.blocks.append(block)
            rec.probs.extend(probs)
            if listener is not None:
                try:
                    listener(block, probs)
                except Exception:
                    log.exception("audio listener failed")

        while True:
            item = self._queue.get()
            try:
                if isinstance(item, np.ndarray):
                    if rec is not None and len(rec.blocks) < self._max_blocks:
                        add(item)
                elif item[0] == "begin":
                    self._vad.reset()
                    rec = Recording()
                    listener = item[2]
                    for b in item[1]:
                        add(b)
                elif item[0] == "cancel":
                    rec, listener = None, None
                elif item[0] is _FINISH:
                    fut = item[1]
                    if rec is not None:
                        rec.probs.extend(self._vad.flush())
                    fut.set_result(rec or Recording())
                    rec, listener = None, None
            except Exception as e:  # never let the worker die
                log.exception("audio worker error")
                if isinstance(item, tuple) and item and item[0] is _FINISH and not item[1].done():
                    item[1].set_exception(e)
                rec = None
