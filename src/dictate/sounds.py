"""Subtle start/stop/cancel/error cues: tiny sine blips written once to WAV, played async."""

from __future__ import annotations

import array
import logging
import math
import sys
import wave
from pathlib import Path

log = logging.getLogger(__name__)

SR = 22_050
CUES: dict[str, list[tuple[float, float]]] = {  # (frequency Hz, duration s) segments
    "start": [(660.0, 0.045), (990.0, 0.06)],
    "stop": [(880.0, 0.045), (660.0, 0.06)],
    "cancel": [(440.0, 0.07)],
    "error": [(330.0, 0.08), (0.0, 0.04), (330.0, 0.08)],
    "nothing": [(520.0, 0.05)],
}


def _render(segments: list[tuple[float, float]], volume: float) -> bytes:
    """Sine segments with short fades, as 16-bit PCM."""
    out = array.array("h")
    for freq, dur in segments:
        n = int(SR * dur)
        fade = min(n // 3, int(SR * 0.012))
        for i in range(n):
            env = min(1.0, i / fade, (n - 1 - i) / fade) if fade else 1.0
            v = math.sin(2 * math.pi * freq * i / SR) * env * volume * 0.6 if freq > 0 else 0.0
            out.append(int(max(-1.0, min(1.0, v)) * 32767))
    if sys.byteorder == "big":
        out.byteswap()
    return out.tobytes()


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
                    f.writeframes(_render(segs, volume))
            self._files[name] = path

    def play(self, name: str) -> None:
        if not self.enabled:
            return
        try:
            if sys.platform == "win32":
                import winsound

                winsound.PlaySound(
                    str(self._files[name]), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT
                )
            elif sys.platform == "darwin":
                import subprocess

                subprocess.Popen(["afplay", str(self._files[name])])
        except Exception:
            log.debug("could not play sound %s", name, exc_info=True)
