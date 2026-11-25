"""ASR backend interface."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from dictate.vad import FloatArray


@dataclass
class Transcript:
    text: str
    language: str | None = None


class ASRBackend(Protocol):
    name: str

    def transcribe(self, audio: FloatArray, *, vocabulary: Sequence[str] = ()) -> Transcript:
        """Transcribe 16 kHz mono float32 audio."""
        ...

    def warmup(self) -> None:
        """Run dummy inference so first real use is as fast as steady state."""
        ...
