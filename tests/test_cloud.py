"""Online mode against a fake provider on 127.0.0.1: the exact requests each service gets (fields, headers,
parameters), how replies and errors are read, choosing models, and one whole dictation through the engine."""

from __future__ import annotations

import json
import re
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from dictate.cloud.chat import ChatCompletionsCleanup
from dictate.cloud.http import HTTPError
from dictate.cloud.providers import PROVIDERS, KeyRejected, choose_models, list_models, looks_like_key, pick_model
from dictate.cloud.speech import CloudSpeech, SpeechError
from dictate.llm.base import LLMError, Message


# -- a fake online service ------------------------------------------------------------------------------
class Fake:
    """Records every request; `routes` maps a path regex to a function (body, headers) -> (status, json)."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.routes: dict[str, Any] = {}
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a: Any) -> None:
                pass

            def _serve(self) -> None:
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n else b""
                headers = {k.lower(): v for k, v in self.headers.items()}
                fake.requests.append({"method": self.command, "path": self.path, "headers": headers, "body": body})
                for pattern, fn in fake.routes.items():
                    if re.fullmatch(pattern, self.path.split("?")[0]):
                        status, reply = fn(body, self.headers)
                        break
                else:
                    status, reply = 404, {"error": {"message": f"no route {self.path}"}}
                data = json.dumps(reply).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = _serve

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()


@pytest.fixture
def fake():
    f = Fake()
    yield f
    f.close()


def form_fields(req: dict[str, Any]) -> list[tuple[str, str | bytes]]:
    """(name, value) of each multipart part, in order; the file's value is its bytes."""
    boundary = re.search(r"boundary=(\S+)", req["headers"]["content-type"]).group(1).encode()
    out: list[tuple[str, str | bytes]] = []
    for part in req["body"].split(b"--" + boundary)[1:-1]:
        head, _, value = part.strip(b"\r\n").partition(b"\r\n\r\n")
        name = re.search(rb'name="([^"]+)"', head).group(1).decode()
        out.append((name, value if b"filename=" in head else value.decode()))
    return out


AUTH_ERROR = {"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}}
SPEECH = (np.sin(np.linspace(0, 400, 16000)) * 0.3).astype(np.float32)  # 1 s


# -- choosing models ------------------------------------------------------------------------------------
def test_preferred_model_first_then_newest_matching_one() -> None:
    assert pick_model(["gpt-4.1-nano", "gpt-5.4-nano", "gpt-6-luna"], ("gpt-6-luna",), ()) == "gpt-6-luna"
    patterns = PROVIDERS["google"].cleanup_patterns
    names = ["gemini-3.6-flash", "gemini-3.10-flash-lite", "gemini-3.7-flash-lite", "gemini-3.7-pro"]
    assert pick_model(names, (), patterns) == "gemini-3.10-flash-lite"  # 3.10 > 3.7, lite preferred
    assert pick_model(["gemini-3.6-flash"], (), patterns) == "gemini-3.6-flash"
    assert pick_model(["text-embedding-3"], (), patterns) == ""


def test_grok_non_reasoning_and_speech_fallbacks() -> None:
    xai = PROVIDERS["xai"]
    speech, cleanup = choose_models(xai, ["grok-4.7", "grok-4.20-0309-reasoning", "grok-4.20-0309-non-reasoning"])
    assert cleanup == "grok-4.20-0309-non-reasoning"
    assert speech == "grok-voice-transcribe-2.0"  # not listed by /v1/models: the documented default
    assert choose_models(PROVIDERS["anthropic"], ["claude-opus-5-5", "claude-haiku-5-5"]) == ("", "claude-opus-5-5")


def test_recognises_keys_on_the_clipboard() -> None:
    assert looks_like_key(PROVIDERS["openai"], "here: sk-proj-abcdefghijklmnopqrstuvwxyz012 thanks").startswith("sk-")
    assert looks_like_key(PROVIDERS["anthropic"], "sk-ant-api03-" + "x" * 30)
    assert looks_like_key(PROVIDERS["google"], "https://example.com") == ""


def test_lists_models_with_each_services_auth(fake: Fake) -> None:
    fake.routes[r"/v1/models"] = lambda b, h: (200, {"data": [{"id": "gpt-transcribe"}, {"id": "gpt-6-luna"}]})
    assert list_models(PROVIDERS["openai"], "sk-1", base_url=fake.url) == ["gpt-transcribe", "gpt-6-luna"]
    assert fake.requests[-1]["headers"]["authorization"] == "Bearer sk-1"

    pages = iter([{"models": [{"name": "models/gemini-3.5-transcribe"}], "nextPageToken": "p2"},
                  {"models": [{"name": "models/gemini-3.7-flash-lite"}]}])  # fmt: skip
    fake.routes[r"/v1beta/models"] = lambda b, h: (200, next(pages))
    got = list_models(PROVIDERS["google"], "AIza-k", base_url=fake.url)
    assert got == ["gemini-3.5-transcribe", "gemini-3.7-flash-lite"]
    assert fake.requests[-1]["headers"]["x-goog-api-key"] == "AIza-k" and "pageToken=p2" in fake.requests[-1]["path"]

    fake.routes[r"/v1/models"] = lambda b, h: (401, {"error": {"message": "Incorrect API key provided"}})
    with pytest.raises(KeyRejected, match="Incorrect API key"):
        list_models(PROVIDERS["xai"], "bad", base_url=fake.url)


def test_lists_claude_models_through_the_sdk(fake: Fake) -> None:
    models = {"data": [{"id": "claude-opus-5-5", "type": "model", "display_name": "Claude Opus 5.5",
                        "created_at": "2026-09-22T00:00:00Z"}],
              "has_more": False, "first_id": "claude-opus-5-5", "last_id": "claude-opus-5-5"}  # fmt: skip
    fake.routes[r"/v1/models"] = lambda b, h: (200, models)
    assert list_models(PROVIDERS["anthropic"], "sk-ant-x", base_url=fake.url) == ["claude-opus-5-5"]
    assert fake.requests[-1]["headers"]["authorization"] == "Bearer sk-ant-x"  # how this SDK version sends keys
    fake.routes[r"/v1/models"] = lambda b, h: (401, AUTH_ERROR)
    with pytest.raises(KeyRejected):
        list_models(PROVIDERS["anthropic"], "sk-ant-bad", base_url=fake.url)


# -- speech to text -------------------------------------------------------------------------------------
def test_openai_transcription_request(fake: Fake) -> None:
    fake.routes[r"/v1/audio/transcriptions"] = lambda b, h: (200, {"text": " hello there ", "languages": []})
    asr = CloudSpeech("openai", "sk-1", "gpt-transcribe", base_url=fake.url)
    t = asr.transcribe(SPEECH, vocabulary=["Okonkwo", "pgvector", "bad<tag>"], language="fr")
    assert t.text == "hello there"
    fields = form_fields(fake.requests[-1])
    names = [n for n, _ in fields]
    assert names == ["model", "languages[]", "keywords[]", "keywords[]", "file"]
    assert dict(fields[:2]) == {"model": "gpt-transcribe", "languages[]": "fr"}
    wav = fields[-1][1]
    assert isinstance(wav, bytes) and wav[:4] == b"RIFF" and wav[8:12] == b"WAVE"
    assert fake.requests[-1]["headers"]["authorization"] == "Bearer sk-1"

    CloudSpeech("openai", "sk-1", "whisper-1", base_url=fake.url).transcribe(SPEECH, vocabulary=["X1"], language="en")
    assert [n for n, _ in form_fields(fake.requests[-1])] == ["model", "language", "prompt", "file"]


def test_xai_transcription_puts_the_file_last(fake: Fake) -> None:
    fake.routes[r"/v1/stt"] = lambda b, h: (200, {"text": "hola", "language": "es", "words": []})
    t = CloudSpeech("xai", "xai-k", base_url=fake.url).transcribe(SPEECH, vocabulary=["Ñandú"], language="es")
    assert t.text == "hola"
    fields = form_fields(fake.requests[-1])
    assert [n for n, _ in fields] == ["model", "language", "keyterm", "file"]
    assert fields[0][1] == "grok-voice-transcribe-2.0" and fields[2][1] == "Ñandú"


def test_gemini_transcription_request(fake: Fake) -> None:
    reply = {"candidates": [{"content": {"parts": [{"audioTranscription": {"text": "new line hi"}}]}}]}
    fake.routes[r"/v1beta/models/gemini-3\.5-transcribe:generateContent"] = lambda b, h: (200, reply)
    t = CloudSpeech("google", "AIza-k", "gemini-3.5-transcribe", base_url=fake.url).transcribe(
        SPEECH, vocabulary=["Kubernetes"], language="en"
    )
    assert t.text == "new line hi"
    req = fake.requests[-1]
    body = json.loads(req["body"])
    assert req["headers"]["x-goog-api-key"] == "AIza-k"
    assert body["generationConfig"]["audioTranscriptionConfig"] == {
        "mode": "VERBATIM", "languageCodes": ["en"], "customVocabulary": ["Kubernetes"]}  # fmt: skip
    assert body["contents"][0]["parts"][0]["inlineData"]["mimeType"] == "audio/wav"


def test_speech_errors_say_what_to_do(fake: Fake) -> None:
    fake.routes[r"/v1/audio/transcriptions"] = lambda b, h: (401, {"error": {"message": "invalid key"}})
    with pytest.raises(SpeechError, match="rejected the API key"):
        CloudSpeech("openai", "sk-x", base_url=fake.url).transcribe(SPEECH)
    fake.routes[r"/v1/audio/transcriptions"] = lambda b, h: (429, {"error": {"message": "quota"}})
    with pytest.raises(SpeechError, match="out of credit"):
        CloudSpeech("openai", "sk-x", base_url=fake.url).transcribe(SPEECH)
    assert CloudSpeech("openai", "k", base_url=fake.url).transcribe(SPEECH[:800]).text == ""  # too short: not sent


def test_claude_does_no_speech() -> None:
    with pytest.raises(ValueError):
        CloudSpeech("anthropic", "k")


# -- the AI cleanup -------------------------------------------------------------------------------------
MSGS = [Message("system", "rules"), Message("user", "ex"), Message("assistant", "<clean>Ex.</clean>"),
        Message("user", "<transcript>um hello</transcript>")]  # fmt: skip


def chat_reply(text: str, finish: str = "stop") -> dict:
    return {"choices": [{"message": {"content": text}, "finish_reason": finish}],
            "usage": {"prompt_tokens": 50, "completion_tokens": 4}}  # fmt: skip


def test_openai_cleanup_request_and_reply(fake: Fake) -> None:
    fake.routes[r"/v1/chat/completions"] = lambda b, h: (200, chat_reply("<clean>Hello.</clean>"))
    llm = ChatCompletionsCleanup("openai", "sk-1", "gpt-6-luna", base_url=fake.url)
    c = llm.chat(MSGS, prefill="<clean>", max_tokens=40, stop=("</clean>",), timeout_s=5)
    assert c.text == "<clean>Hello.</clean>" and not c.truncated and c.generated_tokens == 4
    body = json.loads(fake.requests[-1]["body"])
    assert body["model"] == "gpt-6-luna" and body["reasoning_effort"] == "none" and body["stop"] == ["</clean>"]
    assert body["max_completion_tokens"] == 40 and "max_tokens" not in body
    assert [m["role"] for m in body["messages"]] == ["system", "user", "assistant", "user"]


def test_cleanup_adapts_to_what_the_service_rejects(fake: Fake) -> None:
    seen: list[dict] = []

    def picky(body: bytes, headers: Any) -> tuple[int, dict]:
        b = json.loads(body)
        seen.append(b)
        if b.get("reasoning_effort") in ("none",):
            return 400, {"error": {"message": "Unsupported value: 'reasoning_effort' does not support 'none'."}}
        if "stop" in b:
            return 400, {"error": {"message": "Unsupported parameter: 'stop' is not supported with this model."}}
        return 200, chat_reply("<clean>Hi.</clean>")

    fake.routes[r"/v1/chat/completions"] = picky
    llm = ChatCompletionsCleanup("openai", "sk-1", "gpt-6-luna", base_url=fake.url)
    assert llm.chat(MSGS, max_tokens=40, stop=("</clean>",), timeout_s=5).text == "<clean>Hi.</clean>"
    assert seen[-1]["reasoning_effort"] == "minimal" and "stop" not in seen[-1]
    assert seen[-1]["max_completion_tokens"] == 40 + 1024  # room for a little thinking
    n = len(seen)
    llm.chat(MSGS, max_tokens=40, stop=("</clean>",), timeout_s=5)
    assert len(seen) == n + 1  # remembered: no more retries


def test_grok_and_gemini_cleanup_endpoints(fake: Fake) -> None:
    fake.routes[r"/v1/chat/completions"] = lambda b, h: (200, chat_reply("<clean>A</clean>", "length"))
    c = ChatCompletionsCleanup("xai", "xai-k", "grok-4.20-0309-non-reasoning", base_url=fake.url).chat(
        MSGS, max_tokens=30, timeout_s=5
    )
    body = json.loads(fake.requests[-1]["body"])
    assert c.truncated and "reasoning_effort" not in body and body["max_tokens"] == 30
    fake.routes[r"/v1beta/openai/chat/completions"] = lambda b, h: (200, chat_reply("<clean>B</clean>"))
    ChatCompletionsCleanup("google", "AIza-k", "gemini-3.7-flash-lite", base_url=fake.url).chat(MSGS, timeout_s=5)
    assert fake.requests[-1]["headers"]["authorization"] == "Bearer AIza-k"
    fake.routes[r"/v1/chat/completions"] = lambda b, h: (401, {"error": {"message": "bad key"}})
    with pytest.raises(LLMError, match="401"):
        ChatCompletionsCleanup("xai", "x", "m", base_url=fake.url).chat(MSGS, timeout_s=5)


def claude_reply(text: str, stop_reason: str = "end_turn") -> dict:
    return {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": [{"type": "thinking", "thinking": "", "signature": "s"}, {"type": "text", "text": text}],
            "stop_reason": stop_reason, "stop_sequence": None,
            "usage": {"input_tokens": 12, "output_tokens": 6, "cache_read_input_tokens": 2400,
                      "cache_creation_input_tokens": 0}}  # fmt: skip


def test_claude_cleanup_request(fake: Fake) -> None:
    from dictate.cloud.claude import ClaudeCleanup

    fake.routes[r"/v1/messages"] = lambda b, h: (200, claude_reply("<clean>Hello.</clean>"))
    llm = ClaudeCleanup("sk-ant-k", base_url=fake.url)
    c = llm.chat(MSGS, prefill="<clean>", max_tokens=40, stop=("</clean>",), timeout_s=5)
    assert c.text == "<clean>Hello.</clean>" and c.prompt_evaluated == 12 and c.prompt_tokens == 2412
    req = fake.requests[-1]
    body = json.loads(req["body"])
    assert req["headers"]["authorization"] == "Bearer sk-ant-k"
    assert "server-side-fallback-2026-07-01" in req["headers"]["anthropic-beta"]
    assert body["model"] == "claude-opus-5-5" and body["fallbacks"] == "default"
    assert body["output_config"] == {"effort": "low"} and body["stop_sequences"] == ["</clean>"]
    assert body["max_tokens"] == 40 + 4096 and "temperature" not in body and "thinking" not in body
    assert body["system"] == "rules"
    turns = body["messages"]
    assert [t["role"] for t in turns] == ["user", "assistant", "user"]  # ends on the user: no prefill
    assert [("cache_control" in t["content"][0]) for t in turns] == [False, True, False]


def test_claude_refusal_and_bad_key_fall_back_to_rules(fake: Fake) -> None:
    from dictate.cloud.claude import ClaudeCleanup

    fake.routes[r"/v1/messages"] = lambda b, h: (200, claude_reply("", "refusal"))
    with pytest.raises(LLMError, match="declined"):
        ClaudeCleanup("k", base_url=fake.url).chat(MSGS, timeout_s=5)
    fake.routes[r"/v1/messages"] = lambda b, h: (401, AUTH_ERROR)
    with pytest.raises(LLMError, match="rejected the API key"):
        ClaudeCleanup("k", base_url=fake.url).chat(MSGS, timeout_s=5)
    fake.routes[r"/v1/messages"] = lambda b, h: (200, claude_reply("<clean>x</clean>"))
    ClaudeCleanup("k", "claude-haiku-5-5", base_url=fake.url).chat(MSGS, timeout_s=5)
    body = json.loads(fake.requests[-1]["body"])
    assert "fallbacks" not in body  # only on the models that support the server-side fallback


def test_cleaner_uses_an_online_model(fake: Fake) -> None:
    from dictate.cleanup.llm_cleanup import Cleaner
    from dictate.config import Config

    reply = chat_reply("<clean>So I think we should ship it.</clean>")
    fake.routes[r"/v1/chat/completions"] = lambda b, h: (200, reply)
    cfg = Config()
    res = Cleaner(ChatCompletionsCleanup("openai", "k", "gpt-6-luna", base_url=fake.url), cfg).clean(
        "so um I think we should, like, ship it"
    )
    assert res.used_llm and res.text == "So I think we should ship it."


# -- keys, settings, guard ------------------------------------------------------------------------------
def test_keys_come_from_the_keychain_or_the_environment(monkeypatch) -> None:
    import keyring

    store: dict[tuple[str, str], str] = {}
    monkeypatch.setattr(keyring, "set_password", lambda s, u, p: store.__setitem__((s, u), p))
    monkeypatch.setattr(keyring, "get_password", lambda s, u: store.get((s, u)))
    from dictate.cloud import keys

    monkeypatch.delenv("DICTATE_OPENAI_KEY", raising=False)
    assert keys.get_key("openai") == ""
    keys.set_key("openai", "  sk-abc  ")
    assert store == {("Dictate", "openai"): "sk-abc"} and keys.get_key("openai") == "sk-abc"
    monkeypatch.setenv("DICTATE_OPENAI_KEY", "sk-env")
    assert keys.get_key("openai") == "sk-env"


def test_online_setup_is_complete_only_with_every_key(monkeypatch) -> None:
    from dictate.app import configured
    from dictate.config import parse_config

    have = {"openai": "sk-1"}
    monkeypatch.setattr("dictate.cloud.keys.get_key", lambda p: have.get(p, ""))
    cfg = parse_config('[client]\nmode = "online"\n[online]\nspeech = "openai"\ncleanup = "anthropic"\n')
    assert not configured(cfg)
    have["anthropic"] = "sk-ant-1"
    assert configured(cfg)
    with pytest.raises(Exception, match="online"):
        parse_config('[online]\nspeech = "anthropic"\n')  # Claude doesn't do speech


def test_online_mode_may_only_reach_the_chosen_services(monkeypatch) -> None:
    from dictate import cloud
    from dictate.config import parse_config

    cfg = parse_config('[online]\nspeech = "google"\ncleanup = "anthropic"\n')
    assert cloud.hosts_for(cfg) == ["generativelanguage.googleapis.com", "api.anthropic.com"]
    monkeypatch.setenv("DICTATE_GOOGLE_BASE_URL", "http://proxy.example:8080")
    assert "proxy.example" in cloud.hosts_for(cfg)


def test_http_errors_carry_the_services_message(fake: Fake) -> None:
    from dictate.cloud.http import request

    fake.routes[r"/x"] = lambda b, h: (503, [{"error": {"message": "overloaded"}}])
    with pytest.raises(HTTPError) as e:
        request("GET", fake.url + "/x")
    assert e.value.status == 503 and e.value.message == "overloaded"


# -- one whole dictation in online mode -----------------------------------------------------------------
CLIP = Path(__file__).resolve().parents[1] / "bench" / "asr" / "clips" / "en-commands-01.wav"


def _silero_available() -> bool:
    from dictate import models
    from dictate.config import Config

    return models.silero_path(Config()).is_file()


@pytest.mark.skipif(not CLIP.is_file() or not _silero_available(), reason="needs a local recording + Silero VAD")
def test_a_whole_dictation_in_online_mode(fake: Fake, monkeypatch) -> None:
    """Real speech -> speech detection and phrase cuts on this computer -> each phrase to the (fake) online
    service as WAV -> spoken commands and the online cleanup -> text, over the same WebSocket as always."""
    from websockets.sync.client import connect

    from dictate import modes
    from dictate.config import parse_config

    transcripts = iter(["so um this is a test comma of the online mode"] + ["and more"] * 20)
    fake.routes[r"/v1/audio/transcriptions"] = lambda b, h: (200, {"text": next(transcripts)})
    reply = chat_reply("<clean>This is a test, of the online mode.</clean>")
    fake.routes[r"/v1/chat/completions"] = lambda b, h: (200, reply)
    monkeypatch.setenv("DICTATE_OPENAI_BASE_URL", fake.url)
    monkeypatch.setenv("DICTATE_OPENAI_KEY", "sk-test")
    cfg = parse_config('[client]\nmode = "online"\n[online]\nspeech = "openai"\ncleanup = "openai"\n'
                       'speech_model = "gpt-transcribe"\ncleanup_model = "gpt-6-luna"\n')  # fmt: skip
    cfg.cleanup.skip_when_clean = False
    session = modes.start(cfg)
    try:
        assert session.label == "Online: OpenAI" and "127.0.0.1" in session.allow_hosts
        with wave.open(str(CLIP)) as w:
            assert w.getframerate() == 16000 and w.getnchannels() == 1
            pcm = w.readframes(w.getnframes())
        with connect(f"{session.server}/ws?token={session.token}") as ws:
            ws.send(json.dumps({"type": "start", "language": "en"}))
            for i in range(0, len(pcm), 3200):  # 100 ms at a time, as the client sends it
                ws.send(pcm[i : i + 3200])
            ws.send(json.dumps({"type": "stop", "raw": False}))
            result = json.loads(ws.recv(timeout=30))
    finally:
        session.close()
    stt = [r for r in fake.requests if r["path"] == "/v1/audio/transcriptions"]
    assert stt, "no phrase was sent for transcription"
    for r in stt:
        fields = dict((n, v) for n, v in form_fields(r) if n != "keywords[]")
        assert fields["model"] == "gpt-transcribe" and fields["languages[]"] == "en"
        assert fields["file"][:4] == b"RIFF"
    assert result["type"] == "result", result
    assert "online mode" in result["text"] and result["mode"] in ("cleaned", "rules")
