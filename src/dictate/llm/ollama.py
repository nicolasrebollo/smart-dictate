"""Ollama as a convenience backend (if you already run it). Must be on localhost.

Uses /api/chat; Ollama keeps the model resident and reuses its KV cache for an identical
message prefix. Slower to start than the bundled llama-server and adds its own overhead, so
it isn't the default.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence

from dictate.llm.base import Completion, LLMError, LLMTimeout, Message


class OllamaBackend:
    def __init__(self, url: str, model: str) -> None:
        host = urllib.parse.urlparse(url).hostname or ""
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise LLMError(f"Ollama URL must be on this machine (localhost), got {url!r}")
        self.url = url.rstrip("/")
        self.model = model
        self.name = f"ollama:{model}"

    def chat(
        self,
        messages: Sequence[Message],
        *,
        prefill: str = "",
        max_tokens: int = 256,
        stop: Sequence[str] = (),
        timeout_s: float = 2.5,
    ) -> Completion:
        msgs = [{"role": m.role, "content": m.content} for m in messages]
        if prefill:
            msgs.append({"role": "assistant", "content": prefill})
        payload = {
            "model": self.model,
            "messages": msgs,
            "stream": False,
            "think": False,
            "keep_alive": "60m",
            "options": {"temperature": 0, "top_k": 1, "num_predict": max_tokens, "stop": list(stop)},
        }
        t0 = time.perf_counter()
        req = urllib.request.Request(
            self.url + "/api/chat",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as r:
                data = json.loads(r.read())
        except TimeoutError as e:
            raise LLMTimeout(f"Ollama did not answer within {timeout_s:.1f}s") from e
        except urllib.error.URLError as e:
            raise LLMError(f"Ollama request failed: {e}") from e
        text = str(data.get("message", {}).get("content", ""))
        if prefill and text.startswith(prefill):
            text = text[len(prefill) :]
        return Completion(
            text=text,
            prompt_tokens=int(data.get("prompt_eval_count", 0) or 0),
            prompt_evaluated=int(data.get("prompt_eval_count", 0) or 0),
            generated_tokens=int(data.get("eval_count", 0) or 0),
            prompt_ms=float(data.get("prompt_eval_duration", 0) or 0) / 1e6,
            generate_ms=float(data.get("eval_duration", 0) or 0) / 1e6,
            total_ms=(time.perf_counter() - t0) * 1000,
            truncated=data.get("done_reason") == "length",
        )

    def close(self) -> None:
        pass
