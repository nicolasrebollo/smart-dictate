"""Benchmarks: clip recording, ASR WER/latency (cleanup and e2e harnesses arrive in M2/M3)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from dictate import paths

BENCH_DIR = paths.repo_root() / "bench"
ASR_DIR = BENCH_DIR / "asr"
PROMPTS = ASR_DIR / "prompts.jsonl"
CLIPS_DIR = ASR_DIR / "clips"
REPORTS_DIR = BENCH_DIR / "reports"


@dataclass
class Prompt:
    id: str
    lang: str
    category: str
    style: str
    text: str


def load_prompts(path: Path = PROMPTS) -> list[Prompt]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(Prompt(**json.loads(line)))
    return out
