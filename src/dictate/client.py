"""`dictate` on your Mac or PC: hold the hotkey, speak, release; the text appears in whatever app
has focus (VS Code, browsers, Outlook...). Audio streams to your PC while you speak; the PC sends
back clean text. No models on this device.

Threads: main = tray icon; hotkeys = keyboard gestures; worker = talks to the server and pastes.
The microphone callback and the hotkey thread never wait on the network.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

from dictate import paths
from dictate.config import Config, set_value
from dictate.hotkeys import Action, Event, GestureRecognizer, HotkeyListener, display_key
from dictate.mic import Mic, level_warning
from dictate.sounds import Sounds

log = logging.getLogger("dictate")


def ws_uri(server: str, token: str) -> str:
    """'wss://pc.tailnet.ts.net' -> 'wss://pc.tailnet.ts.net/ws?token=...'"""
    parts = urlsplit(server)
    scheme = parts.scheme
    if scheme == "ws" and (parts.hostname or "").endswith(".ts.net") and parts.port is None:
        scheme = "wss"  # `tailscale serve` only exposes HTTPS: plain ws:// to a .ts.net name just times out
    path = parts.path if parts.path not in ("", "/") else "/ws"
    return urlunsplit((scheme, parts.netloc, path, f"token={quote(token)}", ""))


class Connection:
    """One persistent WebSocket to the server; reconnects when needed."""

    def __init__(self, server: str, token: str, on_change: Callable[[bool, str], None]) -> None:
        self.uri = ws_uri(server, token)
        self.server = server
        self._on_change = on_change
        self._ws: Any = None
        self._send_lock = threading.Lock()
        self._connect_lock = threading.Lock()
        self.results: queue.Queue[dict] = queue.Queue()
        self.last_error = ""

    @property
    def connected(self) -> bool:
        return self._ws is not None

    def ensure(self) -> bool:
        """Connect if needed (blocks up to ~5 s). The connection lives in a reader thread."""
        if self._ws is not None:
            return True
        with self._connect_lock:
            if self._ws is not None:
                return True
            opened = threading.Event()
            threading.Thread(target=self._run, args=(opened,), name="ws-connection", daemon=True).start()
            opened.wait(8)
            return self._ws is not None

    def _run(self, opened: threading.Event) -> None:
        from websockets.exceptions import InvalidStatus
        from websockets.sync.client import connect

        try:
            with connect(self.uri, open_timeout=5, compression=None, max_size=2**20, ping_interval=20) as ws:
                self._ws = ws
                opened.set()
                self._on_change(True, "")
                with contextlib.suppress(Exception):  # closed: handled below
                    for msg in ws:
                        if isinstance(msg, str):
                            with contextlib.suppress(ValueError):
                                self.results.put(json.loads(msg))
        except InvalidStatus as e:
            code = e.response.status_code
            self.last_error = "wrong token (check [client] token)" if code == 401 else f"server said {code}"
            self._on_change(False, self.last_error)
            return
        except (OSError, TimeoutError) as e:
            self.last_error = f"can't reach {self.server} ({e.__class__.__name__})"
            self._on_change(False, self.last_error)
            return
        finally:
            opened.set()
        if self._ws is not None:  # dropped (not closed by us)
            self._ws = None
            self.last_error = f"lost connection to {self.server}"
            self._on_change(False, self.last_error)
            self.results.put({"type": "error", "message": "connection lost"})

    def send(self, data: str | bytes) -> bool:
        ws = self._ws
        if ws is None:
            return False
        try:
            with self._send_lock:
                ws.send(data)
            return True
        except Exception:
            return False

    def close(self) -> None:
        ws, self._ws = self._ws, None
        if ws is not None:
            with contextlib.suppress(Exception):
                ws.close()


def make_inserter(cfg: Config) -> Any:
    c = cfg.client
    if sys.platform == "win32":
        from dictate.insert import TextInserter

        return TextInserter(restore_clipboard=c.restore_clipboard, restore_delay_ms=c.restore_delay_ms)
    if sys.platform == "darwin":
        from dictate.insert_mac import MacInserter

        return MacInserter(restore_clipboard=c.restore_clipboard, restore_delay_ms=c.restore_delay_ms)
    raise SystemExit("The dictate client supports Windows and macOS.")


def mac_permissions_ok() -> bool:
    """Accessibility (to press Cmd+V) and Input Monitoring (to see the hotkey). If either is missing,
    ask macOS to show its permission prompts, and return False."""
    ok = True
    try:
        from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt

        ok = bool(AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})) and ok
    except Exception:
        log.debug("accessibility check unavailable", exc_info=True)
    try:
        import Quartz

        if not Quartz.CGPreflightListenEventAccess():
            Quartz.CGRequestListenEventAccess()
            ok = False
    except Exception:
        log.debug("input monitoring check unavailable", exc_info=True)
    return ok


class Client:
    def __init__(self, cfg: Config, *, token: str | None = None) -> None:
        c = cfg.client
        self.cfg = cfg
        self.language = c.language
        self.inserter = make_inserter(cfg)
        self.sounds = Sounds(paths.data_dir() / "sounds", enabled=c.sounds, volume=c.volume)
        self.conn = Connection(c.server, token if token is not None else c.token, self._connection_changed)
        self.mic = Mic(c.input_device, c.preroll_ms, keep_open=c.keep_mic_open)
        self.recognizer = GestureRecognizer(
            tap_max_s=c.tap_max_ms / 1000, double_tap_window_s=c.double_tap_window_ms / 1000, hands_free=c.hands_free
        )
        self.hotkeys = HotkeyListener(
            self.recognizer, self.on_event, ptt=c.hotkey, cancel=c.cancel, raw_modifier=c.raw_modifier,
            is_injecting=lambda: bool(getattr(self.inserter, "injecting", False)),
        )  # fmt: skip
        self._work: queue.Queue[tuple] = queue.Queue()
        self._stopping = threading.Event()
        self._level_checked = False
        self._level_buf: list[bytes] = []
        self.tray: Any = None
        self._state = ("offline", "Connecting…")

    # -- state / tray ---------------------------------------------------------------------------
    def _set_state(self, state: str, status: str) -> None:
        self._state = (state, status)
        if self.tray is not None:
            self.tray.set_state(state, status)

    def _connection_changed(self, ok: bool, error: str) -> None:
        if ok:
            log.info("connected to %s", self.conn.server)
            self._set_state("ready", "Ready")
        else:
            log.warning("offline: %s", error)
            self._set_state("offline", f"Offline: {error}")

    def _keep_connected(self) -> None:
        while not self._stopping.wait(5):
            if not self.conn.connected:
                self.conn.ensure()

    # -- hotkey thread (never blocks) -----------------------------------------------------------
    def on_event(self, ev: Event) -> None:
        if ev.action == Action.START:
            self._work.put(("start", self.language))
            self.mic.begin(self._on_audio)
            self.sounds.play("start")
            self._set_state("listening", "Listening…")
        elif ev.action == Action.HANDS_FREE:
            self._set_state("listening", "Listening (hands-free): tap the hotkey to stop")
        elif ev.action == Action.STOP:
            self.sounds.play("stop")
            self._set_state("working", "Working…")

            def finish(raw: bool = ev.raw) -> None:  # keep a short tail so the last syllable isn't cut
                self.mic.end()
                self._work.put(("stop", raw))

            threading.Timer(self.cfg.client.release_tail_ms / 1000, finish).start()
        elif ev.action in (Action.CANCEL, Action.DISCARD):
            self.mic.end()
            self._work.put(("cancel",))
            if ev.action == Action.CANCEL:
                self.sounds.play("cancel")
            self._set_state(*(("ready", "Ready") if self.conn.connected else self._state))

    def _on_audio(self, block: bytes) -> None:  # microphone thread: queue only
        self._work.put(("audio", block))
        if not self._level_checked and len(self._level_buf) < 50:  # first 2 s of the first dictation
            self._level_buf.append(block)

    # -- worker thread: network + paste ---------------------------------------------------------------
    def _worker(self) -> None:
        dropping = False  # this dictation couldn't reach the server
        while True:
            item = self._work.get()
            kind = item[0]
            if kind == "quit":
                return
            try:
                if kind == "start":
                    dropping = not (
                        self.conn.ensure() and self.conn.send(json.dumps({"type": "start", "language": item[1]}))
                    )
                    if dropping:
                        self.sounds.play("error")
                elif kind == "audio":
                    if not dropping and not self.conn.send(item[1]):
                        dropping = True
                elif kind == "cancel":
                    if not dropping:
                        self.conn.send(json.dumps({"type": "cancel"}))
                    dropping = False
                elif kind == "stop":
                    self._check_level()
                    if dropping:
                        dropping = False
                        self._set_state("offline", f"Offline: {self.conn.last_error or 'server unreachable'}")
                        continue
                    self._finish(raw=item[1])
            except Exception:
                log.exception("dictation failed")
                self.sounds.play("error")
                dropping = False

    def _finish(self, raw: bool) -> None:
        while not self.conn.results.empty():  # drop anything stale
            self.conn.results.get_nowait()
        t0 = time.perf_counter()
        if not self.conn.send(json.dumps({"type": "stop", "raw": raw})):
            self.sounds.play("error")
            return
        try:
            m = self.conn.results.get(timeout=30)
        except queue.Empty:
            m = {"type": "error", "message": "the server took too long"}
        rtt = (time.perf_counter() - t0) * 1000
        if m.get("type") != "result":
            log.warning("dictation failed: %s", m.get("message"))
            self.sounds.play("error")
            self._set_state(*(("ready", "Ready") if self.conn.connected else self._state))
            return
        text = m.get("text", "")
        if not text:
            self.sounds.play("nothing")
        else:
            self.inserter.insert(text)
        what = f"{len(text.split())} words" if text else "nothing heard"
        shown = f": {text}" if self.cfg.logging.log_transcripts else ""
        log.info("%s in %.0f ms (server %s ms, %s)%s", what, rtt, m.get("ms"), m.get("mode"), shown)
        self._set_state("ready", "Ready")

    def _check_level(self) -> None:
        if self._level_checked or not self._level_buf:
            return
        self._level_checked = True
        warning = level_warning(b"".join(self._level_buf))
        self._level_buf = []
        if warning:
            log.warning(warning)

    # -- tray actions ----------------------------------------------------------------------------------
    def set_language(self, code: str) -> None:
        self.language = code
        if self.cfg.source_path is not None:
            set_value(self.cfg.source_path, "client", "language", code)
        log.info("language: %s", code)

    def open_settings(self) -> None:
        path = self.cfg.source_path
        if path is None:
            return
        if sys.platform == "win32":
            os.startfile(path)
        else:
            subprocess.Popen(["open", "-t", str(path)])

    # -- lifecycle --------------------------------------------------------------------------------------
    def run(self, *, tray: bool = True) -> None:
        if sys.platform == "darwin" and not mac_permissions_ok():
            print("  macOS needs permission: System Settings > Privacy & Security > Accessibility (and Input")
            print("  Monitoring): turn on the app you started dictate from (Terminal), then restart dictate.")
        self.mic.open()
        log.info("microphone: %s", self.mic.name)
        threading.Thread(target=self._worker, name="worker", daemon=True).start()
        try:
            self.hotkeys.start()
        except PermissionError as e:
            self.mic.close()
            raise SystemExit(f"\n  {e}\n") from None
        self.conn.ensure()
        threading.Thread(target=self._keep_connected, name="reconnect", daemon=True).start()
        c = self.cfg.client
        key, cancel = display_key(c.hotkey), display_key(c.cancel)
        print(f"\n  Ready. Hold {key} and speak; release to insert.")
        if c.hands_free:
            print(f"  Double-tap {key} for hands-free; tap it again to stop. {cancel} cancels.")
        print(f"  Server: {c.server}")
        if tray:
            from dictate.tray import Tray

            self.tray = Tray(
                language=self.language, on_language=self.set_language, on_settings=self.open_settings,
                on_quit=self.stop,
            )  # fmt: skip
            self.tray.set_state(*self._state)
            print("  Quit from the tray icon (or Ctrl+C here).\n")
            with contextlib.suppress(KeyboardInterrupt):
                self.tray.run()
        else:
            print("  Ctrl+C to quit.\n")
            try:
                while not self._stopping.wait(0.5):
                    pass
            except KeyboardInterrupt:
                pass
        self.stop()

    def stop(self) -> None:
        if self._stopping.is_set():
            return
        self._stopping.set()
        self.hotkeys.stop()
        self.mic.close()
        self._work.put(("quit",))
        self.inserter.flush()
        self.conn.close()
