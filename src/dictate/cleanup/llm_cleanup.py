"""The LLM cleanup pass: prompt -> LLM -> guardrails -> (cleaned text | fallback)."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from dictate.cleanup import guardrails
from dictate.cleanup.prompt import PREFILL, STOP, PromptBuilder, extract, max_tokens_for
from dictate.cleanup.terms import known_terms
from dictate.config import Config
from dictate.llm.base import Completion, LLMBackend, LLMError, LLMTimeout

log = logging.getLogger(__name__)


@dataclass
class CleanupResult:
    text: str  # cleaned text, or "" when we fell back
    used_llm: bool
    fallback_reason: str = ""
    model_output: str = ""  # what the model said (even if rejected) - for the eval harness
    completion: Completion | None = None
    ms: float = 0.0
    extra: dict[str, float] = field(default_factory=dict)


class Cleaner:
    def __init__(self, backend: LLMBackend, cfg: Config) -> None:
        self.backend = backend
        self.cfg = cfg
        self._builder_key: tuple | None = None
        self._builder: PromptBuilder | None = None
        self._terms = known_terms()

    def builder(self) -> PromptBuilder:
        key = (self.cfg.asr.language, tuple(self.cfg.vocabulary.words), self.cfg.cleanup.math_style)
        if key != self._builder_key:
            self._builder = PromptBuilder(language=key[0], vocabulary=key[1], math_style=key[2])
            self._builder_key = key
        assert self._builder is not None
        return self._builder

    def clean(self, text: str, *, context: str = "general") -> CleanupResult:
        t0 = time.perf_counter()
        if not text.strip():
            return CleanupResult("", False, "empty input")
        messages = self.builder().messages(text, context)
        try:
            c = self.backend.chat(
                messages,
                prefill=PREFILL,
                max_tokens=max_tokens_for(text),
                stop=STOP,
                timeout_s=self.cfg.cleanup.timeout_ms / 1000,
            )
        except LLMTimeout:
            return CleanupResult("", False, "timeout", ms=(time.perf_counter() - t0) * 1000)
        except LLMError as e:
            log.warning("cleanup model failed: %s", e)
            return CleanupResult("", False, "llm error", ms=(time.perf_counter() - t0) * 1000)

        out = extract(c.text)
        ms = (time.perf_counter() - t0) * 1000
        if c.truncated:
            return CleanupResult("", False, "output truncated", out, c, ms)
        allowed = [*self.cfg.vocabulary.words, *self._terms]
        g = guardrails.check(text, out, self.cfg.guardrails, allowed=allowed)
        if not g.ok:
            return CleanupResult("", False, g.reason, out, c, ms)
        return CleanupResult(out, True, "", out, c, ms)

    def warm(self) -> None:
        """Fill the prefix cache (and run the GPU kernels once) so the first real call is fast."""
        for sample in ("Um, this is a warm up.", "Testing the cleanup, uh, once more."):
            self.clean(sample)
