"""The online AI services Dictate can use, and how to pick each one's models.

Model names change every few months, so none is hard-wired: when you set a service up, Dictate asks it
which models your key can use (`list_models`) and picks the best match for each job (`pick_model`):
first the preferred names below, then the newest name that matches the job's pattern. The pick is
saved in the settings file ([online] speech_model / cleanup_model), where it can be changed by hand.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from dictate.cloud.http import HTTPError, request


@dataclass(frozen=True)
class Provider:
    id: str
    name: str  # what people call it
    company: str
    key_url: str  # where to create an API key
    key_pattern: str  # what a key looks like (only used to recognise one on the clipboard)
    hosts: tuple[str, ...]  # the only places Dictate connects to for this service
    base_url: str
    # Speech to text: preferred model names, then patterns (newest version first). Empty: no speech API.
    speech_models: tuple[str, ...] = ()
    speech_patterns: tuple[str, ...] = ()
    # The AI cleanup: fast, inexpensive text models first.
    cleanup_models: tuple[str, ...] = ()
    cleanup_patterns: tuple[str, ...] = ()
    extra: dict = field(default_factory=dict)

    @property
    def does_speech(self) -> bool:
        return bool(self.speech_models or self.speech_patterns)


PROVIDERS: dict[str, Provider] = {
    "openai": Provider(
        id="openai",
        name="OpenAI",
        company="OpenAI (ChatGPT)",
        key_url="https://platform.openai.com/api-keys",
        key_pattern=r"sk-[A-Za-z0-9_\-]{20,}",
        hosts=("api.openai.com",),
        base_url="https://api.openai.com",
        speech_models=("gpt-transcribe", "gpt-4o-transcribe", "whisper-1"),
        cleanup_models=("gpt-6-luna", "gpt-5.6-luna", "gpt-5.4-nano", "gpt-5-nano", "gpt-4.1-nano"),
        cleanup_patterns=(r"^gpt-(\d+(?:\.\d+)?)-luna$", r"^gpt-(\d+(?:\.\d+)?)-nano$", r"^gpt-(\d+(?:\.\d+)?)-mini$"),
    ),
    "anthropic": Provider(
        id="anthropic",
        name="Claude",
        company="Anthropic (Claude)",
        key_url="https://platform.claude.com/",
        key_pattern=r"sk-ant-[A-Za-z0-9_\-]{20,}",
        hosts=("api.anthropic.com",),
        base_url="https://api.anthropic.com",
        # Claude reads text and images, not audio: someone else does the speech part.
        cleanup_models=("claude-opus-5-5",),
        cleanup_patterns=(r"^claude-opus-(\d+(?:-\d+)?)$",),
    ),
    "google": Provider(
        id="google",
        name="Gemini",
        company="Google (Gemini)",
        key_url="https://aistudio.google.com/apikey",
        key_pattern=r"AIza[A-Za-z0-9_\-]{30,}",
        hosts=("generativelanguage.googleapis.com",),
        base_url="https://generativelanguage.googleapis.com",
        speech_models=("gemini-3.5-transcribe",),
        speech_patterns=(r"^gemini-(\d+(?:\.\d+)?)-transcribe$",),
        cleanup_patterns=(r"^gemini-(\d+(?:\.\d+)?)-flash-lite$", r"^gemini-(\d+(?:\.\d+)?)-flash$"),
    ),
    "xai": Provider(
        id="xai",
        name="Grok",
        company="xAI (Grok)",
        key_url="https://console.x.ai/",
        key_pattern=r"xai-[A-Za-z0-9_\-]{20,}",
        hosts=("api.x.ai",),
        base_url="https://api.x.ai",
        speech_models=("grok-voice-transcribe-2.0",),
        speech_patterns=(r"^grok-voice-transcribe-(\d+(?:\.\d+)?)$",),
        cleanup_patterns=(r"^grok-([\d.\-]+)-non-reasoning$", r"^grok-([\d.\-]+)-fast-non-reasoning$"),
    ),
}

SPEECH_DEFAULTS = {p.id: p.speech_models[0] for p in PROVIDERS.values() if p.speech_models}


class KeyRejected(RuntimeError):
    pass


def _version_key(match: re.Match[str]) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", match.group(1) if match.groups() else "0"))


def pick_model(available: list[str], preferred: tuple[str, ...], patterns: tuple[str, ...]) -> str:
    """The first preferred model the key can use, else the newest one matching the first pattern that
    matches anything. "" if nothing fits."""
    have = set(available)
    for name in preferred:
        if name in have:
            return name
    for pattern in patterns:
        found = [(m, name) for name in available if (m := re.match(pattern, name))]
        if found:
            return max(found, key=lambda pair: _version_key(pair[0]))[1]
    return ""


def list_models(provider: Provider, key: str, *, base_url: str | None = None, timeout: float = 15) -> list[str]:
    """Every model this key can use. Raises KeyRejected for a wrong key, HTTPError otherwise."""
    base = (base_url or provider.base_url).rstrip("/")
    try:
        if provider.id == "anthropic":
            return _anthropic_models(key, base, timeout)
        if provider.id == "google":
            names: list[str] = []
            token = ""
            for _ in range(10):  # pages
                url = f"{base}/v1beta/models?pageSize=1000" + (f"&pageToken={token}" if token else "")
                data = request("GET", url, headers={"x-goog-api-key": key}, timeout=timeout)
                names += [m["name"].removeprefix("models/") for m in data.get("models", [])]
                token = data.get("nextPageToken", "")
                if not token:
                    break
            return names
        data = request("GET", f"{base}/v1/models", headers={"Authorization": f"Bearer {key}"}, timeout=timeout)
        return [m["id"] for m in data.get("data", []) if "id" in m]
    except HTTPError as e:
        if e.status in (400, 401, 403) and provider.id != "anthropic":
            raise KeyRejected(f"{provider.name} didn't accept that key ({e.message})") from None
        raise


def _anthropic_models(key: str, base: str, timeout: float) -> list[str]:
    import anthropic

    from dictate.cloud.claude import sdk_http_client

    client = anthropic.Anthropic(
        api_key=key, base_url=base, timeout=timeout, max_retries=1, http_client=sdk_http_client()
    )
    try:
        return [m.id for m in client.models.list()]
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
        raise KeyRejected(f"Claude didn't accept that key ({e.message})") from None
    except anthropic.APIStatusError as e:
        raise HTTPError(e.status_code, e.message) from None
    except anthropic.APIConnectionError as e:
        raise HTTPError(0, f"couldn't reach Claude ({e})") from None


def choose_models(provider: Provider, available: list[str]) -> tuple[str, str]:
    """(speech model, cleanup model) for this provider, "" where it has none that fits.
    Speech has a fallback name: xAI and OpenAI don't always list their speech models."""
    speech = ""
    if provider.does_speech:
        speech = pick_model(available, provider.speech_models, provider.speech_patterns)
        speech = speech or SPEECH_DEFAULTS.get(provider.id, "")
    cleanup = pick_model(available, provider.cleanup_models, provider.cleanup_patterns)
    return speech, cleanup


def looks_like_key(provider: Provider, text: str) -> str:
    """The key in `text` (e.g. the clipboard), or ""."""
    m = re.search(provider.key_pattern, text or "")
    return m.group(0) if m else ""
