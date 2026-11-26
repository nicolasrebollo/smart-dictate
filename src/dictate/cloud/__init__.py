"""Online mode: the speech recognition and the AI cleanup run on an online AI service (OpenAI,
Anthropic's Claude, Google's Gemini or xAI's Grok) with the person's own API key. Speech detection,
spoken commands and formatting still run on this computer."""

from __future__ import annotations

import os

from dictate.cloud.providers import PROVIDERS, Provider

__all__ = ["PROVIDERS", "Provider", "create_cleanup", "create_speech", "hosts_for"]


def hosts_for(cfg) -> list[str]:
    """Where online mode may connect: only the services you chose."""
    from urllib.parse import urlsplit

    hosts: list[str] = []
    for pid in (cfg.online.speech, cfg.online.cleanup):
        if pid in PROVIDERS:
            hosts += PROVIDERS[pid].hosts
            if url := base_url(pid):
                hosts.append(urlsplit(url).hostname or "")
    return list(dict.fromkeys(hosts))


def base_url(pid: str) -> str | None:
    """DICTATE_<SERVICE>_BASE_URL overrides where a service is reached (a proxy, or the tests' fake one)."""
    return os.environ.get(f"DICTATE_{pid.upper()}_BASE_URL") or None


def _key(pid: str) -> str:
    from dictate.cloud.keys import get_key

    key = get_key(pid)
    if not key:
        raise RuntimeError(f"No API key for {PROVIDERS[pid].name}. Choose Change how Dictate works… in the menu.")
    return key


def create_speech(cfg):
    from dictate.cloud.speech import CloudSpeech

    pid = cfg.online.speech
    if pid not in PROVIDERS or not PROVIDERS[pid].does_speech:
        raise RuntimeError("No online service is set up to turn speech into text.")
    return CloudSpeech(pid, _key(pid), cfg.online.speech_model, base_url=base_url(pid))


def create_cleanup(cfg):
    pid = cfg.online.cleanup
    if pid == "anthropic":
        from dictate.cloud.claude import ClaudeCleanup

        return ClaudeCleanup(_key(pid), cfg.online.cleanup_model, base_url=base_url(pid))
    from dictate.cloud.chat import ChatCompletionsCleanup

    if not cfg.online.cleanup_model:
        raise RuntimeError(f"No {PROVIDERS[pid].name} model is set for the cleanup.")
    return ChatCompletionsCleanup(pid, _key(pid), cfg.online.cleanup_model, base_url=base_url(pid))
