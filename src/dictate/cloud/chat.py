"""The AI cleanup by an online text model (an LLMBackend) through the Chat Completions format, which
OpenAI, xAI (Grok) and Google (Gemini, at its /v1beta/openai endpoint) all accept. Claude has its own
backend (cloud/claude.py).

The services disagree on a few parameters (reasoning effort, the name of the length limit, stop
sequences), and they change: when one is rejected, the request is retried without it (or with the
other spelling), and what worked is remembered for the next dictation.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence

from dictate.cloud.http import HTTPError, HTTPTimeout, post_json
from dictate.cloud.providers import PROVIDERS
from dictate.llm.base import Completion, LLMError, LLMTimeout, Message

log = logging.getLogger(__name__)

_CHAT_PATH = {
    "openai": "/v1/chat/completions",
    "xai": "/v1/chat/completions",
    "google": "/v1beta/openai/chat/completions",
}
# Least thinking first: the cleanup is a quick edit, and every second of thinking is a second of waiting.
_EFFORTS = ("none", "minimal", "low")


class ChatCompletionsCleanup:
    def __init__(self, provider: str, key: str, model: str, *, base_url: str | None = None) -> None:
        self.provider = PROVIDERS[provider]
        self.key = key
        self.model = model
        self.url = (base_url or self.provider.base_url).rstrip("/") + _CHAT_PATH[provider]
        self.name = f"{provider}:{model}"
        # what this service accepts (adjusted when it rejects something)
        self.length_param = "max_completion_tokens" if provider == "openai" else "max_tokens"
        self.effort: str | None = None if provider == "xai" else _EFFORTS[0]
        self.send_stop = True

    def chat(
        self,
        messages: Sequence[Message],
        *,
        prefill: str = "",  # not supported by these services: the model writes the opening tag itself
        max_tokens: int = 256,
        stop: Sequence[str] = (),
        timeout_s: float = 10,
    ) -> Completion:
        t0 = time.perf_counter()
        deadline = t0 + timeout_s
        for _attempt in range(5):
            payload: dict = {
                "model": self.model,
                "messages": [{"role": m.role, "content": m.content} for m in messages],
                # thinking tokens count towards the limit on reasoning models: leave room for a little
                self.length_param: max_tokens + (0 if self.effort in (None, "none") else 1024),
            }
            if self.effort:
                payload["reasoning_effort"] = self.effort
            if stop and self.send_stop:
                payload["stop"] = list(stop)[:4]
            remaining = deadline - time.perf_counter()
            if remaining <= 0.2:
                raise LLMTimeout(f"{self.provider.name} didn't answer within {timeout_s:.1f}s")
            try:
                data = post_json(self.url, payload, headers={"Authorization": f"Bearer {self.key}"}, timeout=remaining)
            except HTTPTimeout:
                raise LLMTimeout(f"{self.provider.name} didn't answer within {timeout_s:.1f}s") from None
            except HTTPError as e:
                if e.status == 400 and self._adjust(e.message):
                    continue
                raise LLMError(f"{self.provider.name}: {e.status} {e.message}") from None
            return self._completion(data, t0)
        raise LLMError(f"{self.provider.name} kept rejecting the request")

    def _adjust(self, message: str) -> bool:
        """Change what we send after a 400 that names a parameter. True if there's something to retry."""
        m = message.lower()
        if "reasoning" in m and self.effort is not None:
            i = _EFFORTS.index(self.effort) if self.effort in _EFFORTS else len(_EFFORTS) - 1
            self.effort = _EFFORTS[i + 1] if i + 1 < len(_EFFORTS) else None
            log.info("%s: reasoning effort -> %s", self.name, self.effort)
            return True
        if "max_completion_tokens" in m and self.length_param == "max_completion_tokens":
            self.length_param = "max_tokens"
            return True
        if "max_tokens" in m and self.length_param == "max_tokens":
            self.length_param = "max_completion_tokens"
            return True
        if "stop" in m and self.send_stop:
            self.send_stop = False  # the reply is cut at the closing tag anyway (prompt.extract)
            return True
        return False

    def _completion(self, data: dict, t0: float) -> Completion:
        try:
            choice = data["choices"][0]
            text = choice.get("message", {}).get("content") or ""
        except (KeyError, IndexError, TypeError):
            raise LLMError(f"{self.provider.name} sent an unexpected reply") from None
        usage = data.get("usage") or {}
        ms = (time.perf_counter() - t0) * 1000
        return Completion(
            text=str(text),
            prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
            prompt_evaluated=int(usage.get("prompt_tokens", 0) or 0),
            generated_tokens=int(usage.get("completion_tokens", 0) or 0),
            total_ms=ms,
            truncated=choice.get("finish_reason") == "length",
        )

    def close(self) -> None:
        pass
