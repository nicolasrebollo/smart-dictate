"""Audio -> text: VAD trim -> ASR (chunked at pauses) -> formatting. No hotkeys, no insertion,
so the daemon, `dictate transcribe` and the benchmarks all share exactly this code."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt

from dictate.asr.base import ASRBackend
from dictate.cleanup.formatter import FormatOptions, post_format, post_format_llm, pre_format
from dictate.cleanup.incremental import IncrementalCleaner, join_chunks
from dictate.cleanup.llm_cleanup import Cleaner
from dictate.cleanup.skip import llm_reason
from dictate.config import Config
from dictate.timing import Timings
from dictate.vad import SAMPLE_RATE, FloatArray, SileroVAD, split_at_pauses, trim


@dataclass
class PipelineResult:
    text: str  # what gets inserted
    asr_text: str  # raw ASR output
    audio_s: float
    speech_s: float
    scratch_previous: bool = False
    no_speech: bool = False
    used_llm: bool = False
    fallback_reason: str = ""  # why the LLM result was not used ("" if it was, or if it wasn't tried)
    model_output: str = ""
    skipped_llm: bool = False  # formatted by rules because the text didn't need the model
    locked_chunks: int = 0  # chunks cleaned while the user was still speaking
    chunks: int = 0
    timings: Timings = field(default_factory=Timings)


class Pipeline:
    def __init__(self, cfg: Config, asr: ASRBackend, cleaner: Cleaner | None = None) -> None:
        self.cfg = cfg
        self.asr = asr
        self.cleaner = cleaner

    def process(
        self, audio: FloatArray, probs: npt.ArrayLike, *, raw: bool = False, timings: Timings | None = None
    ) -> PipelineResult:
        t = timings or Timings()
        v = self.cfg.vad
        audio_s = len(audio) / SAMPLE_RATE
        with t.stage("vad"):
            trimmed = trim(
                audio,
                probs,
                threshold=v.threshold,
                pad_ms=v.pad_ms,
                max_pause_ms=v.max_pause_ms,
                min_speech_ms=v.min_speech_ms,
            )
        if trimmed is None:
            return PipelineResult("", "", audio_s, 0.0, no_speech=True, timings=t)

        with t.stage("asr"):
            chunks = split_at_pauses(trimmed.audio, trimmed.probs, self.cfg.asr.max_chunk_s)
            texts = [self.asr.transcribe(c, vocabulary=self.cfg.vocabulary.words).text for c in chunks]
            asr_text = " ".join(s for s in texts if s).strip()

        res = self.clean_text(asr_text, raw=raw, timings=t)
        res.audio_s, res.speech_s, res.chunks = audio_s, trimmed.speech_ms / 1000, len(chunks)
        return res

    def clean_text(self, asr_text: str, *, raw: bool = False, timings: Timings | None = None) -> PipelineResult:
        """Transcript -> final text: commands/fillers -> LLM cleanup (unless raw) -> terms/replacements."""
        t = timings or Timings()
        opts = FormatOptions.from_config(self.cfg)
        with t.stage("format"):
            pre = pre_format(asr_text, opts)
        res = PipelineResult("", asr_text, 0.0, 0.0, scratch_previous=pre.scratch_previous, timings=t)
        use_llm = self.llm_active and not raw and bool(pre.text.strip())
        short = self.cfg.cleanup.skip_short_words
        if (
            use_llm
            and self.cfg.cleanup.skip_when_clean
            and not llm_reason(asr_text, opts.language, short_max_words=short)
        ):
            use_llm = False
            res.skipped_llm = True
        if use_llm:
            assert self.cleaner is not None
            with t.stage("llm"):
                cr = self.cleaner.clean(pre.text)
            res.model_output = cr.model_output
            if cr.used_llm:
                with t.stage("post"):
                    res.text = post_format_llm(cr.text, opts)
                res.used_llm = True
                return res
            res.fallback_reason = cr.fallback_reason
        with t.stage("post"):
            res.text = post_format(pre.text, opts)
        return res

    @property
    def llm_active(self) -> bool:
        return self.cleaner is not None and self.cfg.cleanup.enabled

    def new_incremental(self) -> IncrementalCleaner | None:
        """An incremental cleaner for one streamed utterance (None if not enabled)."""
        if not (self.llm_active and self.cfg.cleanup.incremental):
            return None
        return IncrementalCleaner(lambda text: self.clean_text(text), self.cfg.asr.language)

    def finish_stream(
        self,
        segment_texts: list[str],
        *,
        incremental: IncrementalCleaner | None,
        raw: bool = False,
        timings: Timings | None = None,
    ) -> PipelineResult:
        """Final text for a streamed utterance: locked chunks (cleaned during speech) + cleaned tail."""
        t = timings or Timings()
        asr_text = " ".join(s.strip() for s in segment_texts if s.strip())
        if incremental is None or raw or not self.llm_active:
            if incremental is not None:
                incremental.abandon()
            return self.clean_text(asr_text, raw=raw, timings=t)
        with t.stage("llm_wait"):
            locked, tail = incremental.finalize(segment_texts[-1] if segment_texts else "")
        tail_res = self.clean_text(tail, timings=t) if tail.strip() else None
        parts: list[PipelineResult] = [*locked, *([tail_res] if tail_res else [])]
        res = PipelineResult(join_chunks([r.text for r in parts]), asr_text, 0.0, 0.0, timings=t)
        res.used_llm = any(r.used_llm for r in parts)
        res.skipped_llm = bool(parts) and all(r.skipped_llm for r in parts)
        res.fallback_reason = next((r.fallback_reason for r in parts if r.fallback_reason), "")
        res.scratch_previous = bool(parts) and parts[0].scratch_previous
        res.chunks = len(parts)
        res.locked_chunks = len(locked)
        return res


def vad_probs_offline(vad: SileroVAD, audio: FloatArray) -> npt.NDArray[np.float32]:
    """Run the VAD over a whole file at once (used by `transcribe` and benchmarks, which have no
    live stream to compute probabilities incrementally)."""
    vad.reset()
    probs = vad.feed(audio) + vad.flush()
    return np.asarray(probs, dtype=np.float32)
