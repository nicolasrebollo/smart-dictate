"""Speech to text by an online service (an ASRBackend). Each phrase is sent as a short WAV file as soon
as you pause, so on key release only the last phrase is left to transcribe."""

from __future__ import annotations

import base64
import io
import logging
import wave
from collections.abc import Sequence

import numpy as np

from dictate.asr.base import Transcript
from dictate.cloud.http import HTTPError, HTTPTimeout, multipart, post_json, request
from dictate.cloud.providers import PROVIDERS
from dictate.vad import SAMPLE_RATE, FloatArray

log = logging.getLogger(__name__)


def wav_bytes(audio: FloatArray) -> bytes:
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm)
    return buf.getvalue()


class SpeechError(RuntimeError):
    """Shown to the person (it ends up in the tray menu), so it says what to do."""


class CloudSpeech:
    wants_language = True  # the engine passes the dictation's language and the vocabulary

    def __init__(
        self, provider: str, key: str, model: str = "", *, base_url: str | None = None, timeout_s: float = 20
    ) -> None:
        self.provider = PROVIDERS[provider]
        if not self.provider.does_speech:
            raise ValueError(f"{self.provider.name} doesn't turn speech into text")
        self.key = key
        self.model = model or self.provider.speech_models[0]
        self.base = (base_url or self.provider.base_url).rstrip("/")
        self.timeout_s = timeout_s
        self.name = f"{provider}:{self.model}"
        self.device = "cloud"

    def warmup(self) -> None:
        pass  # nothing to load; the first request opens the connection

    def transcribe(
        self, audio: FloatArray, *, vocabulary: Sequence[str] = (), language: str | None = None
    ) -> Transcript:
        if len(audio) < SAMPLE_RATE // 10:
            return Transcript("", language)
        data = wav_bytes(audio)
        words = [w for w in (v.strip() for v in vocabulary) if w and "\n" not in w and "<" not in w and ">" not in w]
        try:
            if self.provider.id == "google":
                text = self._google(data, words, language)
            elif self.provider.id == "xai":
                text = self._xai(data, words, language)
            else:
                text = self._openai(data, words, language)
        except HTTPTimeout:
            raise SpeechError(f"{self.provider.name} took too long to transcribe. Try again.") from None
        except HTTPError as e:
            raise SpeechError(self._explain(e)) from None
        return Transcript(text.strip(), language)

    def _explain(self, e: HTTPError) -> str:
        n = self.provider.name
        if e.status in (401, 403):
            return f"{n} rejected the API key. Choose Change how Dictate works… in the menu to enter a new one."
        if e.status == 429:
            return f"{n} says you're over your limit or out of credit ({e.message})."
        if e.status == 0:
            return f"Can't reach {n}. Check your internet connection."
        return f"{n} couldn't transcribe this ({e.status}: {e.message})."

    # -- per service ------------------------------------------------------------------------------
    def _openai(self, data: bytes, words: list[str], language: str | None) -> str:
        fields = [("model", self.model)]
        if self.model.startswith("gpt-transcribe"):
            fields += [("languages[]", language)] if language else []
            fields += [("keywords[]", w) for w in words[:100]]
        else:
            fields += [("language", language)] if language else []
            if words:
                fields.append(("prompt", "Vocabulary: " + ", ".join(words[:100])))
        body, ctype = multipart(fields, ("file", "speech.wav", "audio/wav", data))
        reply = request(
            "POST", f"{self.base}/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {self.key}", "Content-Type": ctype}, body=body, timeout=self.timeout_s,
        )  # fmt: skip
        return str(reply.get("text", ""))

    def _xai(self, data: bytes, words: list[str], language: str | None) -> str:
        fields = [("model", self.model)]
        fields += [("language", language)] if language else []
        fields += [("keyterm", w[:50]) for w in words[:100]]
        body, ctype = multipart(fields, ("file", "speech.wav", "audio/wav", data))  # the file must come last
        reply = request(
            "POST", f"{self.base}/v1/stt",
            headers={"Authorization": f"Bearer {self.key}", "Content-Type": ctype}, body=body, timeout=self.timeout_s,
        )  # fmt: skip
        return str(reply.get("text", ""))

    def _google(self, data: bytes, words: list[str], language: str | None) -> str:
        config: dict = {"mode": "VERBATIM"}  # spoken commands ("comma", "new line") must survive as words
        if language:
            config["languageCodes"] = [language]
        if words:
            config["customVocabulary"] = words[:100]
        audio_part = {"inlineData": {"mimeType": "audio/wav", "data": base64.b64encode(data).decode()}}
        payload = {
            "contents": [{"parts": [audio_part]}],
            "generationConfig": {"audioTranscriptionConfig": config},
        }
        reply = post_json(
            f"{self.base}/v1beta/models/{self.model}:generateContent", payload,
            headers={"x-goog-api-key": self.key}, timeout=self.timeout_s,
        )  # fmt: skip
        texts = []
        for cand in reply.get("candidates", [])[:1]:
            for part in cand.get("content", {}).get("parts", []):
                t = part.get("audioTranscription", {}).get("text") or part.get("text") or ""
                texts.append(t)
        return " ".join(texts)
