"""Per-utterance stage timing with time.perf_counter()."""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager


class Timings:
    def __init__(self) -> None:
        self.start = time.perf_counter()
        self.stages: dict[str, float] = {}

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        t0 = time.perf_counter()
        try:
            yield
        finally:
            self.add(name, (time.perf_counter() - t0) * 1000.0)

    def add(self, name: str, ms: float) -> None:
        self.stages[name] = self.stages.get(name, 0.0) + ms

    def total_ms(self) -> float:
        return (time.perf_counter() - self.start) * 1000.0

    def summary(self, total_ms: float | None = None) -> str:
        total = self.total_ms() if total_ms is None else total_ms
        parts = " ".join(f"{k}={v:.0f}ms" if v >= 10 else f"{k}={v:.1f}ms" for k, v in self.stages.items())
        return f"{parts} | total={total:.0f}ms"
