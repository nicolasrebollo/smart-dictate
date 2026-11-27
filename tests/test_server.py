"""Client <-> server protocol over a real WebSocket, with a fake engine (no GPU needed)."""

import asyncio
import contextlib
import json
import threading
import time
import urllib.error
import urllib.request

import pytest
from websockets.sync.client import connect

from dictate.client import Connection, ws_uri
from dictate.engine import Result
from dictate.server import DictateServer

TOKEN = "test-token-123"


class FakeSession:
    def __init__(self, engine, language, source="mic"):
        self.engine, self.language, self.source, self.bytes = engine, language, source, 0
        engine.sessions.append(self)
        self.cancelled = False

    def feed_pcm16(self, data: bytes) -> None:
        self.bytes += len(data)

    def finish(self, raw: bool = False) -> Result:
        text = "" if self.bytes == 0 else f"heard {self.bytes} bytes in {self.language}{' raw' if raw else ''}"
        return Result(text, text, "raw" if raw else "cleaned", 1.0, self.bytes / 32000)

    def cancel(self) -> None:
        self.cancelled = True


class FakeEngine:
    def __init__(self):
        self.sessions = []

    def session(self, language="en", source="mic"):
        return FakeSession(self, language, source)


@pytest.fixture(scope="module")
def server():
    engine = FakeEngine()
    srv = DictateServer(engine, TOKEN)
    ready = threading.Event()
    holder = {}

    def run():
        async def main():
            ev = asyncio.Event()
            task = asyncio.create_task(srv.serve_forever("127.0.0.1", 18765, ev))
            await ev.wait()
            ready.set()
            await task

        holder["loop"] = asyncio.new_event_loop()
        with contextlib.suppress(Exception):
            holder["loop"].run_until_complete(main())

    threading.Thread(target=run, daemon=True).start()
    assert ready.wait(10)
    return engine, "ws://127.0.0.1:18765"


def test_ws_uri() -> None:
    assert ws_uri("wss://pc.tail1.ts.net", "a b") == "wss://pc.tail1.ts.net/ws?token=a%20b"
    assert ws_uri("ws://127.0.0.1:8765/", "t") == "ws://127.0.0.1:8765/ws?token=t"
    assert ws_uri("ws://pc.tail1.ts.net", "t") == "wss://pc.tail1.ts.net/ws?token=t"  # Tailscale is HTTPS-only


def test_wrong_token_is_rejected(server) -> None:
    _, url = server
    with pytest.raises(Exception, match="401"):
        connect(ws_uri(url, "nope"), open_timeout=3)


def test_serves_phone_app_and_icons(server) -> None:
    _, url = server
    base = url.replace("ws://", "http://")
    html = urllib.request.urlopen(base + "/", timeout=3).read().decode()
    assert "<title>Dictate</title>" in html and TOKEN not in html
    assert '<link rel="manifest" href="/manifest.webmanifest">' in html
    # iOS reads the manifest named in the HTML itself, so with the right token it must be baked in there
    html = urllib.request.urlopen(base + f"/?t={TOKEN}", timeout=3).read().decode()
    assert f'<link rel="manifest" href="/manifest.webmanifest?t={TOKEN}">' in html
    html = urllib.request.urlopen(base + "/?t=guess", timeout=3).read().decode()
    assert "guess" not in html and '<link rel="manifest" href="/manifest.webmanifest">' in html
    manifest = json.loads(urllib.request.urlopen(base + "/manifest.webmanifest", timeout=3).read())
    assert manifest["display"] == "standalone" and manifest["start_url"] == "/"
    # the Home Screen app must start with the token, but only the right one is echoed back
    good = json.loads(urllib.request.urlopen(base + f"/manifest.webmanifest?t={TOKEN}", timeout=3).read())
    assert good["start_url"] == f"/?t={TOKEN}"
    bad = json.loads(urllib.request.urlopen(base + "/manifest.webmanifest?t=guess", timeout=3).read())
    assert bad["start_url"] == "/"
    png = urllib.request.urlopen(base + "/apple-touch-icon.png", timeout=3).read()
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    with pytest.raises(urllib.error.HTTPError):
        urllib.request.urlopen(base + "/../secret", timeout=3)


def test_dictation_round_trip(server) -> None:
    engine, url = server
    with connect(ws_uri(url, TOKEN), open_timeout=3) as ws:
        ws.send(json.dumps({"type": "start", "language": "fr"}))
        for _ in range(10):
            ws.send(b"\x00\x01" * 320)
        ws.send(json.dumps({"type": "stop", "raw": False}))
        m = json.loads(ws.recv(timeout=5))
        assert m["type"] == "result" and m["text"] == "heard 6400 bytes in fr" and m["mode"] == "cleaned"
        # several dictations over one connection
        ws.send(json.dumps({"type": "start", "language": "en"}))
        ws.send(json.dumps({"type": "stop", "raw": True}))
        m = json.loads(ws.recv(timeout=5))
        assert m["text"] == "" and m["mode"] == "raw"
        ws.send(json.dumps({"type": "start"}))
        ws.send(b"\x00" * 64)
        ws.send(json.dumps({"type": "cancel"}))
        ws.send(json.dumps({"type": "ping"}))
        assert json.loads(ws.recv(timeout=5))["type"] == "pong"
    assert engine.sessions[-1].cancelled


def test_client_connection_class(server) -> None:
    _, url = server
    states = []
    conn = Connection(url, TOKEN, lambda ok, err: states.append(ok))
    assert conn.ensure() and conn.connected
    assert conn.send(json.dumps({"type": "start", "language": "es"}))
    assert conn.send(b"\x00\x00" * 160)
    assert conn.send(json.dumps({"type": "stop"}))
    m = conn.results.get(timeout=5)
    assert m["text"] == "heard 320 bytes in es"
    conn.close()
    time.sleep(0.1)
    assert states[0] is True


def test_client_reports_unreachable_server() -> None:
    states = []
    conn = Connection("ws://127.0.0.1:1", TOKEN, lambda ok, err: states.append((ok, err)))
    assert conn.ensure() is False
    assert states and states[0][0] is False and "can't reach" in states[0][1]
