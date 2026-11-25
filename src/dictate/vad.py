"""Silero VAD (v6, ONNX, CPU) run incrementally, plus pure trimming/splitting helpers.

The model consumes 512-sample frames at 16 kHz (32 ms) with 64 samples of left context and
a recurrent state. We feed it frame-by-frame *while the user is speaking*, so at key release
only the last few frames are left to process.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import numpy.typing as npt

SAMPLE_RATE = 16_000
FRAME = 512  # samples per VAD frame (32 ms)
CONTEXT = 64
FRAME_MS = FRAME * 1000 / SAMPLE_RATE

FloatArray = npt.NDArray[np.float32]


class SileroVAD:
    def __init__(self, model_path: Path) -> None:
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1
        opts.inter_op_num_threads = 1
        opts.log_severity_level = 3
        self._session = ort.InferenceSession(str(model_path), sess_options=opts, providers=["CPUExecutionProvider"])
        self._sr = np.array(SAMPLE_RATE, dtype=np.int64)
        self.reset()

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros(CONTEXT, dtype=np.float32)
        self._pending = np.zeros(0, dtype=np.float32)

    def feed(self, audio: FloatArray) -> list[float]:
        """Feed any amount of audio; returns one speech probability per completed 512-sample frame."""
        buf = np.concatenate([self._pending, audio.astype(np.float32, copy=False)])
        n_frames = len(buf) // FRAME
        probs: list[float] = []
        for i in range(n_frames):
            frame = buf[i * FRAME : (i + 1) * FRAME]
            x = np.concatenate([self._context, frame])[None, :]
            out, self._state = self._session.run(None, {"input": x, "state": self._state, "sr": self._sr})
            self._context = frame[-CONTEXT:]
            probs.append(float(out[0, 0]))
        self._pending = buf[n_frames * FRAME :]
        return probs

    def flush(self) -> list[float]:
        """Process a trailing partial frame (zero-padded)."""
        if len(self._pending) == 0:
            return []
        tail = np.zeros(FRAME - len(self._pending), dtype=np.float32)
        return self.feed(tail)

    def warmup(self) -> None:
        self.feed(np.zeros(FRAME * 4, dtype=np.float32))
        self.reset()


@dataclass
class TrimResult:
    audio: FloatArray
    probs: npt.NDArray[np.float32]  # VAD probabilities aligned with the trimmed audio
    speech_ms: float
    removed_ms: float


def speech_flags(probs: npt.ArrayLike, threshold: float) -> npt.NDArray[np.bool_]:
    """Hysteresis: enter speech at `threshold`, leave below `threshold - 0.15`."""
    p = np.asarray(probs, dtype=np.float32)
    neg = threshold - 0.15
    flags = np.zeros(len(p), dtype=bool)
    in_speech = False
    for i, v in enumerate(p):
        in_speech = bool(v >= (neg if in_speech else threshold))
        flags[i] = in_speech
    return flags


def _runs(flags: npt.NDArray[np.bool_]) -> list[tuple[int, int, bool]]:
    """(start, end_exclusive, value) runs of a boolean array."""
    runs: list[tuple[int, int, bool]] = []
    if len(flags) == 0:
        return runs
    start = 0
    for i in range(1, len(flags) + 1):
        if i == len(flags) or flags[i] != flags[start]:
            runs.append((start, i, bool(flags[start])))
            start = i
    return runs


def trim(
    audio: FloatArray,
    probs: npt.ArrayLike,
    *,
    threshold: float = 0.5,
    pad_ms: float = 250,
    max_pause_ms: float = 700,
    min_speech_ms: float = 150,
) -> TrimResult | None:
    """Strip leading/trailing silence and shorten long internal pauses.

    `probs[i]` covers audio[i*FRAME:(i+1)*FRAME]. Returns None when there's no real speech.
    """
    flags = speech_flags(probs, threshold)
    speech_frames = int(flags.sum())
    speech_ms = speech_frames * FRAME_MS
    if speech_ms < min_speech_ms:
        return None

    pad = round(pad_ms / FRAME_MS)
    max_pause = max(round(max_pause_ms / FRAME_MS), 2 * pad)
    n_frames = len(flags)
    idx = np.flatnonzero(flags)
    first, last = int(idx[0]), int(idx[-1])
    lo, hi = max(0, first - pad), min(n_frames, last + 1 + pad)

    keep: list[tuple[int, int]] = []  # frame ranges to keep
    for s, e, is_speech in _runs(flags[lo:hi]):
        s, e = s + lo, e + lo
        if is_speech or (e - s) <= max_pause:
            keep.append((s, e))
        else:  # long pause: keep its edges so words aren't cut off
            half = max_pause // 2
            keep.append((s, s + half))
            keep.append((e - (max_pause - half), e))

    p = np.asarray(probs, dtype=np.float32)
    pieces = [audio[s * FRAME : e * FRAME] for s, e in keep]
    kept_probs = [p[s:e] for s, e in keep]
    if hi == n_frames:  # include the sub-frame tail that never got its own probability
        pieces.append(audio[n_frames * FRAME :])
    out = np.concatenate(pieces).astype(np.float32, copy=False) if pieces else np.zeros(0, np.float32)
    return TrimResult(
        audio=out,
        probs=np.concatenate(kept_probs) if kept_probs else np.zeros(0, np.float32),
        speech_ms=speech_ms,
        removed_ms=(len(audio) - len(out)) * 1000 / SAMPLE_RATE,
    )


def split_at_pauses(audio: FloatArray, probs: npt.ArrayLike, max_chunk_s: float) -> list[FloatArray]:
    """Split long audio into chunks of at most `max_chunk_s`, cutting at the quietest frame
    in the last third of each window so words aren't split."""
    max_len = int(max_chunk_s * SAMPLE_RATE)
    if len(audio) <= max_len:
        return [audio]
    p = np.asarray(probs, dtype=np.float32)
    if len(p) < (len(audio) + FRAME - 1) // FRAME:
        p = np.pad(p, (0, (len(audio) + FRAME - 1) // FRAME - len(p)), constant_values=1.0)
    chunks: list[FloatArray] = []
    start = 0
    while len(audio) - start > max_len:
        win_lo = (start + max_len * 2 // 3) // FRAME
        win_hi = (start + max_len) // FRAME
        cut_frame = win_lo + int(np.argmin(p[win_lo:win_hi])) if win_hi > win_lo else win_hi
        cut = max(cut_frame * FRAME, start + FRAME)
        chunks.append(audio[start:cut])
        start = cut
    chunks.append(audio[start:])
    return chunks
