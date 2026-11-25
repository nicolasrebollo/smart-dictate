"""`dictate bench cleanup`: compare cleanup models on bench/cleanup/cases.jsonl (+ your recorded
clips end-to-end) and write a markdown report.

Metrics per model:
  exact            final text == expected
  similarity       mean character similarity (rapidfuzz ratio) of final vs expected
  guardrail rate   share of cases where the model output was rejected (fallback used)
  assistant rate   share of cases where the *model* answered / acted instead of cleaning
                   (judged before guardrails; the target is 0)
  leaked           assistant behaviour that got past the guardrails into the final text
  LLM ms           cleanup call latency (p50 / p95)
  e2e WER          your clips: ASR -> cleanup, vs the written prompt text (numbers formatted)
"""

from __future__ import annotations

import copy
import datetime as dt
import gc
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from rapidfuzz import fuzz

from dictate import models
from dictate.bench import BENCH_DIR, CLIPS_DIR, REPORTS_DIR
from dictate.bench.asr import _vram_used_mib, load_clips, normalize
from dictate.cleanup.guardrails import _ASSISTANT_OPENERS
from dictate.cleanup.llm_cleanup import Cleaner
from dictate.config import Config
from dictate.llm import create_llm
from dictate.pipeline import Pipeline

CASES = BENCH_DIR / "cleanup" / "cases.jsonl"
SEGMENTED = BENCH_DIR / "cleanup" / "segmented.jsonl"


@dataclass
class Case:
    id: str
    lang: str
    category: str
    raw: str
    expected: str
    alt: list[str] = field(default_factory=list)


def load_cases(path: Path = CASES) -> list[Case]:
    return [Case(**json.loads(line)) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _sim(a: str, b: str) -> float:
    return fuzz.ratio(a, b) / 100


def _n_tokens(text: str) -> int:
    return len(re.findall(r"[^\W_][\w'’.-]*", text, re.UNICODE))


def assistant_behaviour(model_output: str, case: Case) -> bool:
    """Did the model answer/act instead of cleaning? (heuristic; flagged cases are listed in the report)

    Output that stays close to the raw transcript or to the expected cleanup is never flagged:
    under-cleaning is a quality problem, not assistant behaviour."""
    out = model_output.strip()
    if not out:
        return False
    if _ASSISTANT_OPENERS.match(out) and not _ASSISTANT_OPENERS.match(case.raw):
        return True
    targets = [case.raw, case.expected, *case.alt]
    if max(_sim(out.lower(), t.lower()) for t in targets) >= 0.75:
        return False
    if _n_tokens(out) > 1.3 * max(_n_tokens(case.raw), _n_tokens(case.expected)) + 3:
        return True  # it added content
    if "?" in case.expected and "?" not in out:
        return True  # a question became an answer
    return case.category in ("question", "instruction")


@dataclass
class ModelResult:
    name: str
    load_s: float = 0.0
    vram_mib: int | None = None
    cache_first_ms: float = 0.0
    cache_second_ms: float = 0.0
    cache_evaluated: tuple[int, int] = (0, 0)
    rows: list[dict] = field(default_factory=list)
    e2e: dict[str, float] = field(default_factory=dict)
    e2e_changes: list[tuple[str, str, str]] = field(default_factory=list)
    segmented: list[dict] = field(default_factory=list)


def _transcribe_clips(cfg: Config) -> list[tuple[str, str, str, str]]:
    """(id, lang, category, asr_text) for every recorded clip. ASR is unloaded afterwards."""
    from dictate.asr import create_asr
    from dictate.pipeline import vad_probs_offline
    from dictate.vad import SileroVAD

    clips = load_clips(CLIPS_DIR)
    if not clips:
        return []
    print(f"transcribing {len(clips)} recorded clips for the end-to-end check...")
    vad = SileroVAD(models.silero_path(cfg))
    asr = create_asr(cfg)
    asr.warmup()
    pipe = Pipeline(cfg, asr)
    out = []
    for c in clips:
        res = pipe.process(c.audio, vad_probs_offline(vad, c.audio), raw=True)
        out.append((c.id, c.lang, c.category, res.asr_text, c.ref))
    del pipe, asr
    gc.collect()
    return out


def _wer(refs: list[str], hyps: list[str]) -> float:
    import jiwer

    return float(jiwer.wer(refs, hyps)) if refs else float("nan")


def run_cleanup_bench(cfg: Config, model_names: list[str], *, with_clips: bool = True) -> Path:
    cases = sorted(load_cases(), key=lambda c: c.lang)  # group by language: one prompt prefix each
    clip_texts = _transcribe_clips(cfg) if with_clips else []
    results: list[ModelResult] = []

    for name in model_names:
        vcfg = copy.deepcopy(cfg)
        vcfg.cleanup.model = name
        vcfg.cleanup.enabled = True
        if not models.llm_path(vcfg).is_file():
            print(f"[{name}] skipped: not downloaded (run `dictate setup --llm {name}`)")
            continue
        r = ModelResult(name)
        print(f"[{name}] starting llama-server...")
        v0 = _vram_used_mib()
        t0 = time.perf_counter()
        llm = create_llm(vcfg)
        r.load_s = time.perf_counter() - t0
        v1 = _vram_used_mib()
        r.vram_mib = (v1 - v0) if v0 is not None and v1 is not None else None
        cleaner = Cleaner(llm, vcfg)
        pipe = Pipeline(vcfg, asr=None, cleaner=cleaner)  # type: ignore[arg-type]
        try:
            # prefix-cache check: same static prefix, two different utterances
            a = cleaner.clean("Um, so this is the first call.")
            b = cleaner.clean("And, uh, this is the second call.")
            r.cache_first_ms, r.cache_second_ms = a.ms, b.ms
            r.cache_evaluated = (
                a.completion.prompt_evaluated if a.completion else 0,
                b.completion.prompt_evaluated if b.completion else 0,
            )
            for case in cases:
                vcfg.asr.language = case.lang
                if case.lang != "en":
                    cleaner.clean("Eh, warm up." if case.lang == "es" else "Euh, échauffement.")
                res = pipe.clean_text(case.raw)
                llm_ms = res.timings.stages.get("llm", 0.0)
                final = res.text
                ok = final == case.expected or final in case.alt
                r.rows.append(
                    {
                        "id": case.id,
                        "category": case.category,
                        "lang": case.lang,
                        "raw": case.raw,
                        "expected": case.expected,
                        "final": final,
                        "model": res.model_output,
                        "exact": ok,
                        "sim": max([_sim(final, case.expected)] + [_sim(final, x) for x in case.alt]),
                        "fallback": res.fallback_reason,
                        "assistant": assistant_behaviour(res.model_output, case),
                        "leaked": res.used_llm and assistant_behaviour(final, case),
                        "ms": llm_ms,
                        "skipped": res.skipped_llm,
                    }
                )
                mark = "ok " if ok else ("FB " if res.fallback_reason else "   ")
                print(f"  {mark}{case.id:16s} {llm_ms:6.0f} ms  {final!r}"[:160])
            r.segmented = _segmented_eval(pipe, vcfg)
            if clip_texts:
                refs, raw_h, llm_h, num_refs, num_raw, num_llm = [], [], [], [], [], []
                for _cid, lang, category, asr_text, ref in clip_texts:
                    vcfg.asr.language = lang
                    rr = pipe.clean_text(asr_text, raw=True).text
                    ll = pipe.clean_text(asr_text).text
                    if category in ("disfluent", "commands"):
                        continue  # their written prompt keeps the fillers / spoken command on purpose
                    if ll != rr:
                        r.e2e_changes.append((_cid, rr, ll))
                    if category == "numbers" and lang != "en":
                        continue  # the es/fr prompts spell numbers out, so digits would count as errors
                    bucket = (num_refs, num_raw, num_llm) if category == "numbers" else (refs, raw_h, llm_h)
                    bucket[0].append(normalize(ref))
                    bucket[1].append(normalize(rr))
                    bucket[2].append(normalize(ll))
                r.e2e = {
                    "wer_raw": _wer(refs, raw_h),
                    "wer_llm": _wer(refs, llm_h),
                    "num_raw": _wer(num_refs, num_raw),
                    "num_llm": _wer(num_refs, num_llm),
                }
        finally:
            llm.close()
        results.append(r)
        gc.collect()
        time.sleep(1.0)  # let the GPU memory be released before the next server starts

    return _write_report(results, len(cases), bool(clip_texts))


def _segmented_eval(pipe: Pipeline, vcfg: Config) -> list[dict]:
    """Incremental (sentence-by-sentence, as during speech) vs single-pass cleanup."""
    rows = []
    for line in SEGMENTED.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        case = json.loads(line)
        vcfg.asr.language = case["lang"]
        segs: list[str] = case["segments"]
        targets = [case["expected"], *case.get("alt", [])]
        single = pipe.clean_text(" ".join(segs)).text
        inc = pipe.new_incremental()
        assert inc is not None
        for seg in segs[:-1]:
            inc.commit(seg)
        locked_before = inc.locked_segments
        res = pipe.finish_stream(segs, incremental=inc)
        rows.append({
            "id": case["id"], "expected": case["expected"], "single": single, "incremental": res.text,
            "single_ok": single in targets, "inc_ok": res.text in targets, "locked": locked_before,
            "segments": len(segs),
        })  # fmt: skip
        print(f"  {'ok ' if res.text in targets else '   '}{case['id']:22s} locked {locked_before}/{len(segs)}  "
              f"{res.text!r}"[:150])  # fmt: skip
    vcfg.asr.language = "en"
    return rows


def _pct(x: float) -> str:
    return f"{x:.0%}" if x == x else "-"


def _write_report(results: list[ModelResult], n_cases: int, with_clips: bool) -> Path:
    lines = [
        f"# Cleanup benchmark - {dt.datetime.now():%Y-%m-%d %H:%M}",
        "",
        f"{n_cases} cases from `bench/cleanup/cases.jsonl`. *Assistant* = the model answered or acted on the "
        "transcript instead of cleaning it (before guardrails). *Leaked* = such output reached the final text. "
        "*Fallback* = guardrails rejected the model output and the rule-formatted transcript was used.",
        "",
        "| model | exact | similarity | fallback | assistant | leaked | LLM p50 ms | LLM p95 ms | VRAM MiB | "
        "load s | e2e WER raw -> cleaned | numbers WER (en) raw -> cleaned |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        rows = r.rows
        if not rows:
            continue
        ms = [x["ms"] for x in rows if x["ms"] > 0]
        e2e = f"{_pct(r.e2e['wer_raw'])} -> {_pct(r.e2e['wer_llm'])}" if r.e2e else "-"
        num = f"{_pct(r.e2e['num_raw'])} -> {_pct(r.e2e['num_llm'])}" if r.e2e else "-"
        exact = np.mean([x["exact"] for x in rows])
        sim = np.mean([x["sim"] for x in rows])
        fallback = np.mean([bool(x["fallback"]) for x in rows])
        assistant = np.mean([x["assistant"] for x in rows])
        vram = r.vram_mib if r.vram_mib is not None else "-"
        lines.append(
            f"| {r.name} | {exact:.0%} | {sim:.3f} | {fallback:.0%} | {assistant:.0%} | "
            f"{sum(x['leaked'] for x in rows)} | {np.percentile(ms, 50):.0f} | {np.percentile(ms, 95):.0f} | "
            f"{vram} | {r.load_s:.1f} | {e2e} | {num} |"
        )
    lines += ["", "Skipped the model (text already clean, rules only):", ""]
    for r in results:
        if r.rows:
            sk = [x for x in r.rows if x.get("skipped")]
            ok = sum(x["exact"] for x in sk)
            lines.append(f"- {r.name}: {len(sk)}/{len(r.rows)} cases skipped, {ok}/{len(sk)} of those exact")
    lines += [
        "",
        "Incremental cleanup (`bench/cleanup/segmented.jsonl`: sentences cleaned as they finish vs one pass):",
        "",
    ]
    for r in results:
        if r.segmented:
            n = len(r.segmented)
            inc_ok = sum(x["inc_ok"] for x in r.segmented)
            single_ok = sum(x["single_ok"] for x in r.segmented)
            locked = sum(x["locked"] for x in r.segmented)
            total = sum(x["segments"] for x in r.segmented)
            lines.append(f"- {r.name}: incremental {inc_ok}/{n} exact, single pass {single_ok}/{n} exact; "
                         f"{locked}/{total} segments were cleaned before the end")  # fmt: skip
    lines += ["", "Prefix cache check (two different utterances, same static prefix):", ""]
    for r in results:
        lines.append(
            f"- {r.name}: call 1 {r.cache_first_ms:.0f} ms ({r.cache_evaluated[0]} prompt tokens computed), "
            f"call 2 {r.cache_second_ms:.0f} ms ({r.cache_evaluated[1]} computed)"
        )

    cats = sorted({x["category"] for r in results for x in r.rows})
    lines += [
        "",
        "## Exact match by category",
        "",
        "| category | " + " | ".join(r.name for r in results) + " |",
        "|---|" + "---|" * len(results),
    ]
    for cat in cats:
        cells = []
        for r in results:
            sel = [x for x in r.rows if x["category"] == cat]
            cells.append(f"{np.mean([x['exact'] for x in sel]):.0%} ({len(sel)})" if sel else "-")
        lines.append(f"| {cat} | " + " | ".join(cells) + " |")

    for r in results:
        bad = [x for x in r.rows if not x["exact"]]
        lines += ["", f"## {r.name}: {len(bad)} cases not exact", ""]
        for x in bad:
            flag = " **ASSISTANT**" if x["assistant"] else ""
            fb = f" (fallback: {x['fallback']})" if x["fallback"] else ""
            lines += [
                f"- **{x['id']}**{flag}{fb}",
                f"  - raw:      `{x['raw']!r}`",
                f"  - expected: `{x['expected']!r}`",
                f"  - final:    `{x['final']!r}`",
            ]
            if x["fallback"] and x["model"]:
                lines.append(f"  - model:    `{x['model']!r}`")

    for r in results:
        bad = [x for x in r.segmented if not x["inc_ok"]]
        if bad:
            lines += ["", f"## {r.name}: incremental cleanup misses", ""]
            for x in bad:
                lines += [f"- **{x['id']}** (locked {x['locked']}/{x['segments']})",
                          f"  - expected:    `{x['expected']!r}`", f"  - incremental: `{x['incremental']!r}`",
                          f"  - single pass: `{x['single']!r}`"]  # fmt: skip
    for r in results:
        if r.e2e_changes:
            lines += ["", f"## {r.name}: what cleanup changed on your recorded clips", ""]
            for cid, before, after in r.e2e_changes:
                lines += [f"- **{cid}**", f"  - rule-based: `{before!r}`", f"  - cleaned:    `{after!r}`"]

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORTS_DIR / f"cleanup-{dt.datetime.now():%Y%m%d-%H%M%S}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n" + "\n".join(lines[: 7 + len(results)]))
    print("\n".join(lines[7 + len(results) : 10 + 2 * len(results)]))
    if not with_clips:
        print("(no recorded clips found: end-to-end columns skipped)")
    print(f"\nreport: {out}")
    return out
