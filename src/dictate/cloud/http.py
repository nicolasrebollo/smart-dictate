"""Small HTTPS helpers for the online services (standard library only).

Certificates are checked against the computer's own trusted certificates, like a browser does (see
client.tls_context): Python's built-in list is empty in the Mac app.
"""

from __future__ import annotations

import json
import secrets
import ssl
import urllib.error
import urllib.request
from functools import cache
from typing import Any


class HTTPError(RuntimeError):
    """The service answered with an error (status > 0) or couldn't be reached (status 0)."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class HTTPTimeout(HTTPError):
    def __init__(self, message: str = "the online service took too long") -> None:
        super().__init__(0, message)


@cache
def _tls() -> ssl.SSLContext:
    from dictate.client import tls_context

    return tls_context("wss://")


def _error_message(body: bytes) -> str:
    """The human part of an error reply. The services answer {"error": {"message": ...}} or similar."""
    try:
        data = json.loads(body)
    except ValueError:
        return body.decode("utf-8", "replace").strip()[:300]
    if isinstance(data, list) and data:
        data = data[0]
    if isinstance(data, dict):
        err = data.get("error", data)
        if isinstance(err, dict):
            return str(err.get("message") or err.get("detail") or err)[:300]
        return str(err)[:300]
    return str(data)[:300]


def request(
    method: str, url: str, *, headers: dict[str, str] | None = None, body: bytes | None = None, timeout: float = 20
) -> Any:
    """Send a request and return the decoded JSON reply. Raises HTTPError / HTTPTimeout."""
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_tls()) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        raise HTTPError(e.code, _error_message(e.read())) from None
    except TimeoutError:
        raise HTTPTimeout() from None
    except urllib.error.URLError as e:
        if isinstance(e.reason, TimeoutError):
            raise HTTPTimeout() from None
        raise HTTPError(0, f"couldn't reach the service ({e.reason})") from None
    try:
        return json.loads(raw) if raw else {}
    except ValueError:
        raise HTTPError(0, "the service sent an unexpected reply") from None


def post_json(url: str, payload: dict, *, headers: dict[str, str], timeout: float = 20) -> Any:
    return request(
        "POST", url, headers={**headers, "Content-Type": "application/json"}, body=json.dumps(payload).encode(),
        timeout=timeout,
    )  # fmt: skip


def multipart(fields: list[tuple[str, str]], file: tuple[str, str, str, bytes]) -> tuple[bytes, str]:
    """A multipart/form-data body: the fields in order, then the file (name, filename, type, data) last,
    which is what xAI requires and what everyone else accepts."""
    boundary = "dictate-" + secrets.token_hex(12)
    parts: list[bytes] = []
    for name, value in fields:
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    name, filename, ctype, data = file
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
        f"Content-Type: {ctype}\r\n\r\n".encode()
        + data
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"
