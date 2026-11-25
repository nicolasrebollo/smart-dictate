"""Keep the GPU clocked up while the user is speaking.

Measured on RTX 4060: after >1-2 s idle the GPU drops to a low power state and the next
15 s transcription takes ~390 ms instead of ~100 ms. Running a tiny dummy inference every
~75 ms while recording keeps it at ~140 ms. (M3's live preview does real work on the same
cadence and replaces this.) Only used on CUDA; costs a few watts, only while recording.
"""

from __future__ import annotations

import logging
import threading
import time

import numpy as np

from dictate.asr.base import ASRBackend
from dictate.vad import SAMPLE_RATE

log = logging.getLogger(__name__)


class KeepWarm:
    def __init__(self, asr: ASRBackend, lock: threading.Lock, *, period_s: float = 0.075) -> None:
        self._asr = asr
        self._lock = lock  # held by the real transcription; keep-warm never waits on it
        self._period = period_s
        self._active = threading.Event()
        self._dummy = (np.random.default_rng(0).standard_normal(SAMPLE_RATE // 2) * 1e-3).astype(np.float32)
        self._thread = threading.Thread(target=self._run, name="gpu-keepwarm", daemon=True)
        self._thread.start()

    def start(self) -> None:
        self._active.set()

    def stop(self) -> None:
        self._active.clear()

    def _run(self) -> None:
        while True:
            self._active.wait()
            if self._lock.acquire(blocking=False):
                try:
                    self._asr.transcribe(self._dummy)
                except Exception:
                    log.debug("keep-warm inference failed", exc_info=True)
                finally:
                    self._lock.release()
            time.sleep(self._period)
