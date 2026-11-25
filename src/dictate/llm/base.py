"""LLM backend interface."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


class LLMError(RuntimeError):
    pass


class LLMTimeout(LLMError):
    pass


@dataclass
class Completion:
    text: str
    prompt_tokens: int = 0  # tokens in the prompt
    prompt_evaluated: int = 0  # tokens actually computed (the rest came from the prefix cache)
    generated_tokens: int = 0
    prompt_ms: float = 0.0
    generate_ms: float = 0.0
    total_ms: float = 0.0
    truncated: bool = False  # hit max_tokens before the stop sequence


@dataclass
class Message:
    role: str  # "system" | "user" | "assistant"
    content: str


class LLMBackend(Protocol):
    name: str

    def chat(
        self,
        messages: Sequence[Message],
        *,
        prefill: str = "",
        max_tokens: int = 256,
        stop: Sequence[str] = (),
        timeout_s: float = 2.5,
    ) -> Completion:
        """Greedy completion of a chat. `prefill` starts the assistant's reply (not returned).

        Implementations must keep the rendered prompt for an identical message prefix
        byte-identical so the runtime's KV prefix cache is reused across calls."""
        ...

    def close(self) -> None: ...
