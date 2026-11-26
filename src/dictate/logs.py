"""Logging setup: concise console output plus a rotating file. Audio is never logged; transcript
text only when [logging] log_transcripts = true. Secrets never are: tokens, links and API keys are
hidden in every line (people share log files when something goes wrong)."""

from __future__ import annotations

import logging
import logging.handlers
import re
import sys

from dictate import paths

_SECRETS = [
    (re.compile(r"([?&#](?:token|t)=)[^\s&#\"']+"), r"\1<hidden>"),  # the server token in URLs and links
    (re.compile(r"(token\s*[=:]\s*[\"']?)[A-Za-z0-9_\-]{12,}"), r"\1<hidden>"),
    (re.compile(r"\b(sk-(?:ant-|proj-)?|xai-|AIza)[A-Za-z0-9_\-]{12,}"), r"\1<hidden>"),  # API keys
    (re.compile(r"(Bearer\s+|x-api-key:\s*|x-goog-api-key:\s*)[A-Za-z0-9_\-.]{12,}", re.IGNORECASE), r"\1<hidden>"),
]


def redact(text: str) -> str:
    for pattern, replacement in _SECRETS:
        text = pattern.sub(replacement, text)
    return text


class RedactingFormatter(logging.Formatter):
    """Formats the whole line (message, arguments and tracebacks), then hides any secret in it."""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def setup_logging(debug: bool = False) -> None:
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.DEBUG if debug else logging.INFO)

    if sys.stdout is not None:  # None when started without a console (autostart via pythonw)
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(RedactingFormatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S"))
        root.addHandler(console)

    try:
        d = paths.log_dir()
        d.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            d / "dictate.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
        )
        fh.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(fh)
    except OSError:
        pass

    for noisy in ("urllib3", "httpx", "httpcore", "httpx2", "httpcore2", "anthropic", "huggingface_hub", "filelock"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
