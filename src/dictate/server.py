"""`dictate server`: the PC with the GPU. Clients stream audio in, get clean text back.

Protocol (WebSocket at /ws?token=...):
  client -> {"type": "start", "language": "en"}        begin a dictation
              (+ "source": "computer" when it's the computer's own audio: no spoken commands)
              (+ "live": true to type as you speak: "partial" messages while speaking)
  client -> binary frames                                16 kHz mono PCM16 little-endian, while speaking
  client -> {"type": "stop", "raw": false}               done: returns the result
  client -> {"type": "cancel"}                           discard
  server -> {"type": "partial", "text": " Next sentence."}   live: type this now (it's final)
  server -> {"type": "result", "text": "...", "heard": "...", "mode": "cleaned", "ms": 182}
              (live: "text" is only what's left to type, and "live": true says so; an older server
              has neither, and its "text" is the whole dictation)
  server -> {"type": "error", "message": "..."}

HTTP GET / serves the phone web app. The token comes from the URL you open once (/?t=...); only
when it's the right one does the page name a manifest whose start_url carries it, so that
"Add to Home Screen" keeps it. The server binds to 127.0.0.1 by default: Tailscale (`tailscale serve`) forwards your own
devices to it over HTTPS, and nothing on your Wi-Fi can reach it.
"""

from __future__ import annotations

import asyncio
import hmac
import io
import json
import logging
import time
from functools import cache
from http import HTTPStatus
from importlib import resources
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit

from websockets.asyncio.server import ServerConnection, serve
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response

log = logging.getLogger(__name__)


# -- static files for the phone web app -------------------------------------------------------------
@cache
def _icon_png(size: int) -> bytes:
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (size, size), (24, 24, 27))
    d = ImageDraw.Draw(img)
    s = size / 100
    d.rounded_rectangle((38 * s, 18 * s, 62 * s, 60 * s), radius=12 * s, fill=(244, 244, 245))
    d.arc((28 * s, 34 * s, 72 * s, 72 * s), 0, 180, fill=(244, 244, 245), width=max(2, round(5 * s)))
    d.line((50 * s, 72 * s, 50 * s, 82 * s), fill=(244, 244, 245), width=max(2, round(5 * s)))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


MANIFEST = {
    "name": "Dictate",
    "short_name": "Dictate",
    "start_url": "/",
    "display": "standalone",
    "background_color": "#18181b",
    "theme_color": "#18181b",
    "icons": [
        {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png"},
        {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png"},
    ],
}


def _static(path: str, token: str = "") -> tuple[bytes, str] | None:
    """`token` is set only when the request carried the right one (?t=...)."""
    t = quote(token, safe="")
    if path in ("/", "/index.html"):
        html = resources.files("dictate.web").joinpath("index.html").read_bytes()
        if t:  # iOS reads the manifest named in the HTML; a link swapped in later by script is ignored
            html = html.replace(b'href="/manifest.webmanifest"', f'href="/manifest.webmanifest?t={t}"'.encode(), 1)
        return html, "text/html; charset=utf-8"
    if path == "/manifest.webmanifest":
        # Add to Home Screen saves start_url, and a Home Screen app has its own storage: it must carry the token.
        return json.dumps({**MANIFEST, "start_url": f"/?t={t}" if t else "/"}).encode(), "application/manifest+json"
    sizes = {"/icon-192.png": 192, "/icon-512.png": 512, "/apple-touch-icon.png": 180}
    if path in sizes:
        return _icon_png(sizes[path]), "image/png"
    return None


def _response(status: HTTPStatus, body: bytes, ctype: str) -> Response:
    headers = Headers(
        [
            ("Content-Type", ctype),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-cache"),
            ("X-Content-Type-Options", "nosniff"),
            # The page's address carries the token: never pass it on, never load anything from elsewhere,
            # and never let another site put the page in a frame.
            ("Referrer-Policy", "no-referrer"),
            ("X-Frame-Options", "DENY"),
            (
                "Content-Security-Policy",
                # blob: is the microphone's audio processor (an AudioWorklet the page builds itself)
                "default-src 'self'; script-src 'self' 'unsafe-inline' blob:; style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; connect-src 'self' wss: ws:; frame-ancestors 'none'; base-uri 'none'; "
                "form-action 'none'",
            ),
        ]
    )
    return Response(status.value, status.phrase, headers, body)


# -- server -------------------------------------------------------------------------------------------
class DictateServer:
    def __init__(self, engine, token: str) -> None:
        self.engine = engine
        self.token = token
        self.port = 0  # the port it listens on, once started (useful with port 0: any free port)

    def _authorized(self, request: Request) -> bool:
        given = parse_qs(urlsplit(request.path).query).get("token", [""])[0]
        return bool(self.token) and hmac.compare_digest(given.encode(), self.token.encode())

    async def process_request(self, connection: ServerConnection, request: Request) -> Response | None:
        path = urlsplit(request.path).path
        if path == "/ws":
            if not self._authorized(request):
                return _response(HTTPStatus.UNAUTHORIZED, b"wrong or missing token\n", "text/plain")
            return None  # proceed with the WebSocket handshake
        if request.headers.get("Upgrade", "").lower() == "websocket":
            return _response(HTTPStatus.NOT_FOUND, b"not found\n", "text/plain")
        # Only echo the token back (into the manifest link and start_url) when it's the right one.
        given = parse_qs(urlsplit(request.path).query).get("t", [""])[0]
        ok = bool(given) and bool(self.token) and hmac.compare_digest(given.encode(), self.token.encode())
        static = _static(path, self.token if ok else "")
        if static is None:
            return _response(HTTPStatus.NOT_FOUND, b"not found\n", "text/plain")
        return _response(HTTPStatus.OK, *static)

    async def handle(self, ws: ServerConnection) -> None:
        session = None
        peer = ws.remote_address
        log.debug("client connected: %s", peer)
        try:
            async for msg in ws:
                if isinstance(msg, bytes):
                    if session is not None:
                        session.feed_pcm16(msg)  # sub-millisecond: speech detection + queueing only
                    continue
                try:
                    m = json.loads(msg)
                except ValueError:
                    continue
                kind = m.get("type")
                if kind == "start":
                    if session is not None:
                        session.cancel()
                    on_text = None
                    if m.get("live"):
                        loop = asyncio.get_running_loop()

                        def on_text(text: str, ws: Any = ws, loop: Any = loop) -> None:  # cleanup thread
                            msg = json.dumps({"type": "partial", "text": text})
                            asyncio.run_coroutine_threadsafe(ws.send(msg), loop)

                    session = self.engine.session(
                        str(m.get("language", "en")), str(m.get("source", "mic")), on_text=on_text
                    )
                elif kind == "stop" and session is not None:
                    s, session = session, None
                    live = bool(getattr(s, "live", False))
                    t0 = time.perf_counter()
                    try:
                        r = await asyncio.to_thread(s.finish, bool(m.get("raw", False)))
                        ms = round((time.perf_counter() - t0) * 1000)
                        reply = {"type": "result", "text": r.text, "heard": r.heard, "mode": r.mode, "ms": ms}
                        if live:
                            reply["live"] = True
                    except Exception as e:  # never let one dictation take the server down
                        log.exception("dictation failed")
                        reply = {"type": "error", "message": f"server error: {e}"}
                    await ws.send(json.dumps(reply))
                elif kind == "cancel" and session is not None:
                    session.cancel()
                    session = None
                elif kind == "ping":
                    await ws.send(json.dumps({"type": "pong"}))
        except ConnectionClosed:
            pass
        finally:
            if session is not None:
                session.cancel()
            log.debug("client disconnected: %s", peer)

    async def serve_forever(self, host: str, port: int, ready: asyncio.Event | None = None) -> None:
        async with serve(
            self.handle,
            host,
            port,
            process_request=self.process_request,
            max_size=2**20,
            compression=None,  # audio doesn't compress; skip the latency
            ping_interval=20,
        ) as server:
            self.port = next(iter(server.sockets)).getsockname()[1]
            if ready is not None:
                ready.set()
            await server.serve_forever()


def start_in_thread(srv: DictateServer, host: str, port: int, timeout_s: float = 30) -> int:
    """Run `srv` in a background thread. Returns the port once it listens; raises what it failed with."""
    import threading

    started = threading.Event()
    failure: list[BaseException] = []

    def run() -> None:
        async def main() -> None:
            ready = asyncio.Event()
            task = asyncio.create_task(srv.serve_forever(host, port, ready))
            waiter = asyncio.create_task(ready.wait())
            await asyncio.wait([task, waiter], return_when=asyncio.FIRST_COMPLETED)
            started.set()
            await task

        try:
            asyncio.run(main())
        except BaseException as e:
            failure.append(e)
            started.set()

    threading.Thread(target=run, name="server", daemon=True).start()
    started.wait(timeout_s)
    if failure:
        raise failure[0]
    return srv.port


def tailscale_name() -> str | None:
    """This PC's Tailscale HTTPS name (e.g. my-pc.tail1234.ts.net), if Tailscale is installed."""
    import shutil
    import subprocess

    # Standard install locations too: a terminal opened before Tailscale was installed has an old PATH.
    candidates = [
        shutil.which("tailscale"),
        r"C:\Program Files\Tailscale\tailscale.exe",
        "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
    ]
    exe = next((c for c in candidates if c and Path(c).is_file()), None)
    if exe is None:
        return None
    try:
        out = subprocess.run([exe, "status", "--json"], capture_output=True, text=True, timeout=5).stdout
        name = json.loads(out).get("Self", {}).get("DNSName", "")
        return name.rstrip(".") or None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
