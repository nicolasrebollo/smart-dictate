"""Subtle start/stop/cancel/error cues: tiny sine blips written once to WAV, played async."""

from __future__ import annotations

import logging
import sys
import wave
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

SR = 22_050
CUES: dict[str, list[tuple[float, float]]] = {  # (frequency Hz, duration s) segments
    "start": [(660.0, 0.045), (990.0, 0.06)],
    "stop": [(880.0, 0.045), (660.0, 0.06)],
    "cancel": [(440.0, 0.07)],
    "error": [(330.0, 0.08), (0.0, 0.04), (330.0, 0.08)],
    "nothing": [(520.0, 0.05)],
}


def _render(segments: list[tuple[float, float]], volume: float) -> np.ndarray:
    parts = []
    for freq, dur in segments:
        n = int(SR * dur)
        t = np.arange(n) / SR
        tone = np.sin(2 * np.pi * freq * t) if freq > 0 else np.zeros(n)
        fade = min(n // 3, int(SR * 0.012))
        env = np.ones(n)
        if fade:
            env[:fade] = np.linspace(0, 1, fade)
            env[-fade:] = np.linspace(1, 0, fade)
        parts.append(tone * env)
    pcm = np.concatenate(parts) * volume * 0.6
    return (np.clip(pcm, -1, 1) * 32767).astype(np.int16)


class Sounds:
    def __init__(self, cache_dir: Path, *, enabled: bool = True, volume: float = 0.25) -> None:
        self.enabled = enabled
        self._files: dict[str, Path] = {}
        cache_dir.mkdir(parents=True, exist_ok=True)
        vol_tag = round(volume * 100)
        for name, segs in CUES.items():
            path = cache_dir / f"{name}-{vol_tag}.wav"
            if not path.exists():
                with wave.open(str(path), "wb") as f:
                    f.setnchannels(1)
                    f.setsampwidth(2)
                    f.setframerate(SR)
                    f.writeframes(_render(segs, volume).tobytes())
            self._files[name] = path

    def play(self, name: str) -> None:
        if not self.enabled or sys.platform != "win32":
            return
        import winsound

        try:
            winsound.PlaySound(
                str(self._files[name]), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT
            )
        except Exception:
            log.debug("could not play sound %s", name, exc_info=True)
