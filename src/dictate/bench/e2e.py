"""Real-time replay benchmarks (M3).

`dictate bench e2e`        release-to-text latency per stage, preview on vs off (and streaming off)
`dictate bench streaming`  preview lag, flicker, and streamed vs whole-utterance accuracy

Audio is fed through the same Recorder -> VAD -> StreamingSession path as the live app, at
real-time speed, then finished exactly like App._process (minus pasting, measured separately).
Test material: your recorded clips, plus a ~1 minute "long dictation" made by joining several
clips with natural pauses.
"""

from __future__ import annotations

import datetime as dt
import threading
import time
from dataclasses import dataclass, field

import numpy as np

from dictate import models
from dictate.asr import create_asr
from dictate.asr.keepwarm import KeepWarm
from dictate.audio import BLOCK, Recorder
from dictate.bench import CLIPS_DIR, REPORTS_DIR
from dictate.bench.asr import Clip, load_clips, normalize
from dictate.cleanup.llm_cleanup import Cleaner
from dictate.config import Config
from dictate.llm import create_llm
from dictate.pipeline import Pipeline, PipelineResult
from dictate.streaming import ASRWorker, StreamConfig, StreamingSession
from dictate.timing import Timings
from dictate.vad import SAMPLE_RATE, SileroVAD


class _Flags:
    input_overflow = False


def _gpu_util() -> int | None:
    import subprocess

    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        ).stdout  # fmt: skip
        return int(out.strip().splitlines()[0])
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


def wait_for_quiet_gpu(max_wait_s: float = 90, threshold: int = 15) -> bool:
    """Other GPU work (games, ML jobs in WSL/Docker) makes latency numbers meaningless. While we're
    idle, wait until the GPU is quiet for two samples in a row. Returns False if it never got quiet."""
    deadline = time.monotonic() + max_wait_s
    quiet = 0
    while time.monotonic() < deadline:
        u = _gpu_util()
        if u is None:
            return True  # can't tell (no NVIDIA tools): assume fine
        quiet = quiet + 1 if u < threshold else 0
        if quiet >= 2:
            return True
        time.sleep(0.5)
    return False


@dataclass
class ReplayResult:
    result: PipelineResult | None
    timings: Timings
    total_ms: float
    previews: list[tuple[float, int]] = field(default_factory=list)  # (seconds since start, words shown)
    stats: object | None = None
    asr_text: str = ""
    contended: bool = False  # another program was using the GPU heavily when this run started


class Harness:
    """Loads models once; replays audio through the live pipeline in different modes."""

    def __init__(self, cfg: Config, *, cleanup: bool = True) -> None:
        self.cfg = cfg
        self.vad = SileroVAD(models.silero_path(cfg))
        self.vad.warmup()
        self.asr = create_asr(cfg)
        self.asr.warmup()
        self.llm = None
        cleaner = None
        if cleanup and cfg.cleanup.enabled:
            self.llm = create_llm(cfg)
            cleaner = Cleaner(self.llm, cfg)
            cleaner.warm()
        self.pipeline = Pipeline(cfg, self.asr, cleaner)
        self.lock = threading.Lock()
        self.worker = ASRWorker(self.asr, self.lock)
        self.keep_warm = KeepWarm(self.asr, self.lock)
        self.recorder = Recorder(self.vad, always_open=True)  # never started: we drive the callback

    def close(self) -> None:
        if self.llm is not None:
            self.llm.close()

    def replay(self, audio: np.ndarray, *, streaming: bool, preview: bool, gap_s: float = 1.5) -> ReplayResult:
        c = self.cfg
        contended = not wait_for_quiet_gpu()
        previews: list[tuple[float, int]] = []
        t0 = time.perf_counter()

        def on_preview(committed: str, stable: list[str], unstable: list[str]) -> None:
            previews.append((time.perf_counter() - t0, len(committed.split()) + len(stable) + len(unstable)))

        session = incremental = None
        if streaming:
            incremental = self.pipeline.new_incremental()
            session = StreamingSession(
                self.worker,
                StreamConfig(
                    threshold=c.vad.threshold, pause_ms=c.streaming.segment_pause_ms,
                    min_segment_s=c.streaming.min_segment_s, max_segment_s=c.streaming.max_segment_s,
                    pad_ms=c.vad.pad_ms, max_pause_ms=c.vad.max_pause_ms, min_speech_ms=c.vad.min_speech_ms,
                    preview=preview, preview_interval_ms=c.preview.interval_ms, language=c.asr.language,
                ),
                on_preview=on_preview if preview else None,
                on_commit=(lambda _i, text: incremental.commit(text)) if incremental else None,
            )  # fmt: skip
        use_keep_warm = not (streaming and preview)
        self.recorder.begin(listener=session.feed if session else None)
        if use_keep_warm:
            self.keep_warm.start()
        t0 = time.perf_counter()
        for i in range(0, len(audio) - BLOCK + 1, BLOCK):
            self.recorder._callback(audio[i : i + BLOCK, None], BLOCK, None, _Flags())
            target = t0 + (i + BLOCK) / SAMPLE_RATE
            while (now := time.perf_counter()) < target:
                time.sleep(min(0.002, target - now))
        t_release = time.perf_counter()
        fut = self.recorder.stop(c.audio.release_tail_ms)
        self.keep_warm.stop()
        t = Timings()
        t.start = t_release
        with t.stage("finalize"):
            rec = fut.result(timeout=10)
        res: PipelineResult | None
        asr_text = ""
        if session is not None:
            with t.stage("asr"):
                texts = session.finalize()
            asr_text = " ".join(x for x in texts if x.strip())
            res = self.pipeline.finish_stream(texts, incremental=incremental, timings=t) if asr_text else None
        else:
            with self.lock:
                res = self.pipeline.process(rec.audio(), rec.probs, timings=t)
            asr_text = res.asr_text
        total = t.total_ms()
        time.sleep(gap_s)  # idle between utterances, like a real user
        return ReplayResult(res, t, total, previews, session.stats if session else None, asr_text, contended)


# -- material ---------------------------------------------------------------------------------------
def _material(clips_dir=CLIPS_DIR) -> list[Clip]:
    clips = {c.id: c for c in load_clips(clips_dir)}
    if not clips:
        raise SystemExit("No recorded clips. Record some with `dictate bench record`.")
    picks = [cid for cid in ("en-short-03", "en-med-01", "en-long-02", "en-long-01") if cid in clips]
    out = [clips[cid] for cid in picks]
    long_ids = [cid for cid in ("en-long-01", "en-med-02", "en-long-03", "en-med-05") if cid in clips]
    if len(long_ids) >= 2:
        pause = np.zeros(int(0.9 * SAMPLE_RATE), np.float32)
        parts: list[np.ndarray] = []
        for cid in long_ids:
            parts += [clips[cid].audio, pause]
        out.append(
            Clip("long-dictation", "en", "long", " ".join(clips[c].ref for c in long_ids), np.concatenate(parts))
        )
    return out


def _fmt_ms(values: list[float]) -> str:
    return f"{np.median(values):.0f}" if values else "-"


# -- dictate bench e2e --------------------------------------------------------------------------------
def run_e2e_bench(cfg: Config, *, repeat: int = 2) -> None:
    clips = _material()
    h = Harness(cfg)
    modes = [
        ("streaming + preview", True, True),
        ("streaming, no preview", True, False),
        ("no streaming", False, False),
    ]
    rows = []
    try:
        for clip in clips:
            for label, streaming, preview in modes:
                runs = [h.replay(clip.audio, streaming=streaming, preview=preview) for _ in range(repeat)]
                stages: dict[str, list[float]] = {}
                for r in runs:
                    for k, v in r.timings.stages.items():
                        stages.setdefault(k, []).append(v)
                last = runs[-1].result
                rows.append((clip, label, [r.total_ms for r in runs], stages, last, any(r.contended for r in runs)))
                tot = [r.total_ms for r in runs]
                print(f"  {clip.id:16s} {clip.seconds:5.1f}s  {label:22s} total p50 {np.median(tot):6.0f} ms  "
                      f"{runs[-1].timings.summary(runs[-1].total_ms)}")  # fmt: skip
    finally:
        h.close()

    lines = [
        f"# End-to-end latency - {dt.datetime.now():%Y-%m-%d %H:%M}",
        "",
        f"Key release -> final text, real-time replay of your clips (median of {repeat} runs, 1.5 s idle between "
        "runs). Pasting adds ~11 ms. Stages: finalize = release tail + last audio; asr = last segment (streaming) or "
        "whole utterance; llm_wait = waiting for sentences cleaned during speech; llm = cleaning the remaining tail.",
        "",
        "| clip | audio s | mode | total ms | finalize | asr | llm_wait | llm | chunks cleaned during speech |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for clip, label, totals, stages, last, contended in rows:
        locked = f"{last.locked_chunks}/{last.chunks}" if last and last.chunks else "-"
        flag = " ⚠ GPU busy" if contended else ""
        lines.append(
            f"| {clip.id} | {clip.seconds:.1f} | {label} | **{np.median(totals):.0f}**{flag} | "
            f"{_fmt_ms(stages.get('finalize', []))} | {_fmt_ms(stages.get('asr', []))} | "
            f"{_fmt_ms(stages.get('llm_wait', []))} | {_fmt_ms(stages.get('llm', []))} | {locked} |"
        )
    _write("e2e", lines)


# -- dictate bench streaming ----------------------------------------------------------------------------
def _word_end_times(asr: object, audio: np.ndarray) -> tuple[str, list[float]]:
    r = asr._model.with_timestamps().recognize(audio, sample_rate=SAMPLE_RATE)  # type: ignore[attr-defined]
    ends: list[float] = []
    for tok, ts in zip(r.tokens or [], r.timestamps or [], strict=False):
        if tok.startswith(" ") or not ends:
            ends.append(ts + 0.08)
        else:
            ends[-1] = ts + 0.08
    return r.text, ends


def run_streaming_bench(cfg: Config) -> None:
    import jiwer

    clips = [c for c in load_clips(CLIPS_DIR) if c.lang == "en" and c.seconds >= 3] + [
        c for c in _material() if c.id == "long-dictation"
    ]
    h = Harness(cfg, cleanup=False)
    lags: list[float] = []
    revised = stable = moved = 0
    refs, whole, streamed = [], [], []
    rows = []
    try:
        for clip in clips:
            whole_text, ends = _word_end_times(h.asr, clip.audio)
            r = h.replay(clip.audio, streaming=True, preview=True, gap_s=0.5)
            clip_lags = []
            for i, end in enumerate(ends):
                shown = next((t for t, n in r.previews if n > i), None)
                if shown is not None:
                    clip_lags.append(max(0.0, shown - end))
            lags += clip_lags
            st = r.stats
            revised += st.revised_words
            stable += st.stable_words
            moved += st.moved_words
            refs.append(normalize(clip.ref))
            whole.append(normalize(whole_text))
            streamed.append(normalize(r.asr_text))
            rows.append((clip, st, clip_lags, jiwer.wer(normalize(whole_text), normalize(r.asr_text))))
            lag50 = np.median(clip_lags) if clip_lags else float("nan")
            print(f"  {clip.id:16s} {clip.seconds:5.1f}s  segments {st.segments}  previews {st.previews} "
                  f"(skipped {st.previews_skipped})  lag p50 {lag50:.2f}s")  # fmt: skip
    finally:
        h.close()

    lines = [
        f"# Streaming quality - {dt.datetime.now():%Y-%m-%d %H:%M}",
        "",
        f"{len(clips)} English clips replayed in real time with the live preview on.",
        "",
        "| metric | value | target |",
        "|---|---|---|",
        f"| preview lag p50 (word spoken -> shown) | {np.percentile(lags, 50):.2f} s | < 1 s |",
        f"| preview lag p90 | {np.percentile(lags, 90):.2f} s | |",
        f"| flicker: stable words later revised | {revised}/{stable} ({revised / max(1, stable):.1%}) | ~0 |",
        f"| stable words that moved to the next phrase (kept on screen, not revised) | {moved} | |",
        f"| WER whole-utterance ASR vs reference | {jiwer.wer(refs, whole):.1%} | |",
        f"| WER streamed (segmented) ASR vs reference | {jiwer.wer(refs, streamed):.1%} | close to whole |",
        f"| streamed vs whole-utterance (difference) | {jiwer.wer(whole, streamed):.1%} | |",
        "",
        "| clip | s | segments | previews (skipped) | lag p50 s | streamed vs whole WER |",
        "|---|---|---|---|---|---|",
    ]
    for clip, st, clip_lags, w in rows:
        lag = f"{np.median(clip_lags):.2f}" if clip_lags else "-"
        previews = f"{st.previews} ({st.previews_skipped})"
        lines.append(f"| {clip.id} | {clip.seconds:.1f} | {st.segments + 1} | {previews} | {lag} | {w:.1%} |")
    _write("streaming", lines)


def _write(kind: str, lines: list[str]) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORTS_DIR / f"{kind}-{dt.datetime.now():%Y%m%d-%H%M%S}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n" + "\n".join(lines))
    print(f"\nreport: {out}")


__all__ = ["Harness", "run_e2e_bench", "run_streaming_bench"]
