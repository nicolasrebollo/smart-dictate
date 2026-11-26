"""The AI cleanup by Claude (an LLMBackend), through Anthropic's Python SDK.

- Effort "low": the cleanup is a quick edit. Claude Opus 5.5 always thinks a little; low keeps that short.
- No prefilled reply (current models don't take one): the few-shot replies teach the model to answer
  inside <clean>...</clean>, and the stop sequence ends the reply at the closing tag.
- The rules and examples are the same on every request, so a cache breakpoint after them lets each
  dictation reuse them (faster, and cache reads cost a fraction of normal input).
- Server-side fallback ("default"): if a safety classifier declines a request, Anthropic re-runs it on
  its recommended fallback model instead of failing. Only on the models that support it.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any

from dictate.llm.base import Completion, LLMError, LLMTimeout, Message

DEFAULT_MODEL = "claude-opus-5-5"
_FALLBACK_MODELS = {"claude-fable-5-2", "claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
_FALLBACK_BETA = "server-side-fallback-2026-07-01"
_THINKING_ROOM = 4096  # thinking counts towards max_tokens: leave room so the answer isn't cut off


def sdk_http_client() -> Any:
    """The SDK's HTTP client, checking certificates against the computer's own trusted list (like the rest of
    Dictate): the Mac app's Python has an empty list of its own."""
    import anthropic

    from dictate.cloud.http import _tls

    return anthropic.DefaultHttpxClient(verify=_tls())


class ClaudeCleanup:
    def __init__(self, key: str, model: str = DEFAULT_MODEL, *, base_url: str | None = None) -> None:
        import anthropic

        self._anthropic = anthropic
        kwargs: dict[str, Any] = {
            "api_key": key,
            "max_retries": 0,  # a retry would blow the time budget
            "http_client": sdk_http_client(),
        }
        if base_url:
            kwargs["base_url"] = base_url
        self.client = anthropic.Anthropic(**kwargs)
        self.model = model or DEFAULT_MODEL
        self.name = f"anthropic:{self.model}"

    @staticmethod
    def _request_messages(messages: Sequence[Message]) -> tuple[str, list[dict[str, Any]]]:
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        turns = [m for m in messages if m.role != "system"]
        out: list[dict[str, Any]] = []
        for i, m in enumerate(turns):
            block: dict[str, Any] = {"type": "text", "text": m.content}
            if i == len(turns) - 2:  # the last few-shot reply: everything up to here is the same every time
                block["cache_control"] = {"type": "ephemeral"}
            out.append({"role": m.role, "content": [block]})
        return system, out

    def chat(
        self,
        messages: Sequence[Message],
        *,
        prefill: str = "",  # not supported by current Claude models (see the module docstring)
        max_tokens: int = 256,
        stop: Sequence[str] = (),
        timeout_s: float = 10,
    ) -> Completion:
        a = self._anthropic
        system, turns = self._request_messages(messages)
        extra: dict[str, Any] = {}
        if self.model in _FALLBACK_MODELS:
            extra = {"betas": [_FALLBACK_BETA], "fallbacks": "default"}
        t0 = time.perf_counter()
        try:
            resp = self.client.beta.messages.create(
                model=self.model,
                max_tokens=max_tokens + _THINKING_ROOM,
                system=system,
                messages=turns,
                stop_sequences=list(stop),
                output_config={"effort": "low"},
                timeout=timeout_s,
                **extra,
            )
        except a.APITimeoutError:
            raise LLMTimeout(f"Claude didn't answer within {timeout_s:.1f}s") from None
        except (a.AuthenticationError, a.PermissionDeniedError) as e:
            raise LLMError(f"Claude rejected the API key ({e.message})") from None
        except a.RateLimitError as e:
            raise LLMError(f"Claude: over the rate limit or out of credit ({e.message})") from None
        except a.APIStatusError as e:
            raise LLMError(f"Claude: {e.status_code} {e.message}") from None
        except a.APIConnectionError as e:
            raise LLMError(f"couldn't reach Claude ({e})") from None
        if resp.stop_reason == "refusal":
            raise LLMError("Claude declined to edit this text")  # the rule-based formatting is used instead
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        usage = resp.usage
        cached = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        uncached = int(getattr(usage, "input_tokens", 0) or 0)
        return Completion(
            text=text,
            prompt_tokens=cached + uncached + int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
            prompt_evaluated=uncached,
            generated_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            total_ms=(time.perf_counter() - t0) * 1000,
            truncated=resp.stop_reason == "max_tokens",
        )

    def close(self) -> None:
        self.client.close()
