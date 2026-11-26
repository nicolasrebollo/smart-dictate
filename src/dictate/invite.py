"""The link the PC hands out (`https://pc.tailnet.ts.net/?t=TOKEN`) sets up every device: the phone
opens it, the desktop app asks for it on first start. This turns it into the client's settings."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit, urlunsplit


def parse_invite(text: str) -> tuple[str, str] | None:
    """'https://pc.tailnet.ts.net/?t=TOKEN' -> ('wss://pc.tailnet.ts.net', 'TOKEN'). Also accepts the
    '#t=' form and a 'wss://…/ws?token=…' address. None if it isn't a Dictate link."""
    text = text.strip().strip("<>\"'“”‘’ ")
    m = re.search(r"(?:https?|wss?)://\S+", text)
    if not m:
        return None
    parts = urlsplit(m.group(0).rstrip(".,;)"))
    if not parts.hostname:
        return None
    query = parse_qs(parts.query) | parse_qs(parts.fragment)
    token = (query.get("t") or query.get("token") or [""])[0].strip()
    if not token or not re.fullmatch(r"[\w\-.~%]+", token):
        return None
    scheme = {"https": "wss", "http": "ws"}.get(parts.scheme, parts.scheme)
    return urlunsplit((scheme, parts.netloc, "", "", "")), token


def check_server(server: str, token: str, timeout: float = 6.0) -> str:
    """Try to connect. Returns '' if it works, otherwise a sentence saying what's wrong."""
    from websockets.exceptions import InvalidStatus
    from websockets.sync.client import connect

    from dictate.client import ws_uri

    try:
        with connect(ws_uri(server, token), open_timeout=timeout, compression=None):
            return ""
    except InvalidStatus as e:
        if e.response.status_code == 401:
            return "The PC turned the link down: it's out of date. Ask for a new link."
        return f"The PC answered with an error ({e.response.status_code})."
    except (OSError, TimeoutError):
        return (
            "Can't reach the PC. Check that Tailscale is on and connected on this computer, "
            "and that the PC is on and running Dictate."
        )
    except Exception as e:  # anything unexpected: say so rather than crash the setup
        return f"Couldn't connect ({e.__class__.__name__})."
