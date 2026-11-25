"""`dictate bench asr`: WER + latency per ASR variant over the recorded clips -> markdown report."""

from __future__ import annotations

import copy
import datetime as dt
import gc
import json
import re
import shutil
import subprocess
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

from dictate import models
from dictate.asr import create_asr
from dictate.bench import CLIPS_DIR, REPORTS_DIR
from dictate.cleanup.formatter import FILLERS
from dictate.config import Config
from dictate.pipeline import Pipeline, vad_probs_offline
from dictate.vad import SAMPLE_RATE, SileroVAD

VARIANTS = {
    "cuda": ("cuda", ""),
    "cuda-int8": ("cuda", "int8"),
    "cpu": ("cpu", ""),
    "cpu-int8": ("cpu", "int8"),
}

_FILLER_RX = re.compile(r"\b(?:" + "|".join(sorted({f for v in FILLERS.values() for f in v})) + r")\b")


def normalize(text: str) -> str:
    """Lowercase, strip accents-insensitive punctuation and filler sounds, so WER measures words."""
    t = unicodedata.normalize("NFKC", text).lower()
    t = t.replace("’", "'")
    t = re.sub(r"(?<=\d)[:.,](?=\d)", "", t)  # 5:30 == 5.30 == 530
    t = re.sub(r"[-–—/]", " ", t)
    t = re.sub(r"[^\w\s']", " ", t)
    t = t.replace("'", "")
    t = _FILLER_RX.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip()


@dataclass
class Clip:
    id: str
    lang: str
    category: str
    ref: str
    audio: np.ndarray

    @property
    def seconds(self) -> float:
        return len(self.audio) / SAMPLE_RATE


def load_clips(clips_dir: Path) -> list[Clip]:
    clips = []
    for meta_path in sorted(clips_dir.glob("*.json")):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        wav = meta_path.with_suffix(".wav")
        if not wav.exists():
            continue
        audio, sr = sf.read(wav, dtype="float32", always_2d=True)
        if sr != SAMPLE_RATE:
            raise SystemExit(f"{wav}: expected 16 kHz, got {sr}")
        clips.append(Clip(meta["id"], meta["lang"], meta.get("category", ""), meta["ref"], audio.mean(axis=1)))
    return clips


def _vram_used_mib() -> int | None:
    if not shutil.which("nvidia-smi"):
        return None
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout
        return int(out.strip().splitlines()[0])
    except (OSError, ValueError, subprocess.SubprocessError, IndexError):
        return None


def _pct(values: list[float], q: float) -> float:
    return float(np.percentile(values, q)) if values else float("nan")


def run_asr_bench(cfg: Config, *, variants: list[str], repeat: int = 3, clips_dir: Path | None = None) -> Path:
    import jiwer

    clips = load_clips(clips_dir or CLIPS_DIR)
    if not clips:
        raise SystemExit(f"No clips in {clips_dir or CLIPS_DIR}. Record some with `dictate bench record`.")
    vad = SileroVAD(models.silero_path(cfg))
    probs = {c.id: vad_probs_offline(vad, c.audio) for c in clips}

    lines = [
        f"# ASR benchmark - {dt.datetime.now():%Y-%m-%d %H:%M}",
        "",
        f"{len(clips)} clips, {sum(c.seconds for c in clips):.0f} s of audio, model `{cfg.asr.model}`. "
        f"Latency = VAD trim + ASR on the whole clip (median of {repeat} runs, after warmup). "
        "WER is computed on normalized text (case, punctuation and filler sounds ignored). "
        "Clips in the `numbers` category are scored separately: their references are written form "
        "(5:30 PM) while ASR may output spoken form (five thirty pm); turning one into the other is the "
        "cleanup step's job, so it is not counted as a recognition error.",
        "",
        "| variant | load s | VRAM MiB | WER | WER en | WER es | WER fr | WER numbers | "
        "p50 ms | p95 ms | max ms | "
        "p50 ms (clips >= 10 s) |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    per_clip: dict[str, dict[str, tuple[float, float, str]]] = {}

    for name in variants:
        if name not in VARIANTS:
            raise SystemExit(f"unknown variant {name!r}; choose from {', '.join(VARIANTS)}")
        device, quant = VARIANTS[name]
        vcfg = copy.deepcopy(cfg)
        vcfg.asr.device, vcfg.asr.quantization = device, quant
        if models.missing_models(vcfg):
            print(f"[{name}] skipped: model files missing (run `dictate setup --all`)")
            continue
        print(f"[{name}] loading...")
        vram0 = _vram_used_mib()
        t0 = time.perf_counter()
        asr = create_asr(vcfg)
        asr.warmup()
        load_s = time.perf_counter() - t0
        vram1 = _vram_used_mib()
        pipe = Pipeline(vcfg, asr)

        lat: list[float] = []
        lat_long: list[float] = []
        refs: dict[str, list[str]] = {"all": [], "en": [], "es": [], "fr": [], "numbers": []}
        hyps: dict[str, list[str]] = {"all": [], "en": [], "es": [], "fr": [], "numbers": []}
        for c in clips:
            runs = []
            res = None
            for _ in range(repeat):
                t = time.perf_counter()
                res = pipe.process(c.audio, probs[c.id])
                runs.append((time.perf_counter() - t) * 1000)
            ms = float(np.median(runs))
            lat.append(ms)
            if c.seconds >= 10:
                lat_long.append(ms)
            hyp = res.asr_text if res else ""
            r, h = normalize(c.ref), normalize(hyp)
            for key in ("numbers",) if c.category == "numbers" else ("all", c.lang):
                refs[key].append(r)
                hyps[key].append(h)
            clip_wer = jiwer.wer(r, h) if r else 0.0
            per_clip.setdefault(c.id, {})[name] = (clip_wer, ms, hyp)
            print(f"  {c.id:20s} {c.seconds:5.1f}s  {ms:6.0f} ms  WER {clip_wer:5.1%}")

        def wer(key: str, refs: dict[str, list[str]] = refs, hyps: dict[str, list[str]] = hyps) -> str:
            return f"{jiwer.wer(refs[key], hyps[key]):.1%}" if refs[key] else "-"

        vram = f"{vram1 - vram0}" if vram0 is not None and vram1 is not None and device == "cuda" else "0"
        long_p50 = f"{_pct(lat_long, 50):.0f}" if lat_long else "-"
        lines.append(
            f"| {name} | {load_s:.1f} | {vram} | {wer('all')} | {wer('en')} | {wer('es')} | {wer('fr')} | "
            f"{wer('numbers')} | "
            f"{_pct(lat, 50):.0f} | {_pct(lat, 95):.0f} | {max(lat):.0f} | {long_p50} |"
        )
        del pipe, asr
        gc.collect()

    lines += [
        "",
        "## Per clip",
        "",
        "| clip | lang | s | " + " | ".join(f"{v} WER / ms" for v in variants) + " |",
        "|---|---|---|" + "---|" * len(variants),
    ]
    for c in clips:
        cells = []
        for v in variants:
            if v in per_clip.get(c.id, {}):
                w, ms, _ = per_clip[c.id][v]
                cells.append(f"{w:.0%} / {ms:.0f}")
            else:
                cells.append("-")
        lines.append(f"| {c.id} | {c.lang} | {c.seconds:.1f} | " + " | ".join(cells) + " |")

    lines += ["", "## Errors (first variant)", ""]
    first = next((v for v in variants if any(v in d for d in per_clip.values())), None)
    for c in clips:
        if first and first in per_clip.get(c.id, {}):
            w, _, hyp = per_clip[c.id][first]
            if w > 0:
                lines += [f"- **{c.id}** ({w:.0%})", f"  - ref: {c.ref}", f"  - hyp: {hyp}"]

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORTS_DIR / f"asr-{dt.datetime.now():%Y%m%d-%H%M%S}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n" + "\n".join(lines[: 8 + len(variants)]))
    print(f"\nreport: {out}")
    return out
