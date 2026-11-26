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


def tls_context(uri: str) -> Any:
    """For wss://, check the PC's certificate against the computer's own trusted certificates (macOS
    Keychain, Windows certificate store), like a browser does. Python's built-in default has none on a
    Mac outside Homebrew, including in the installed app, so every connection failed there with
    SSLCertVerificationError."""
    if not uri.startswith("wss://"):
        return None
    import ssl

    try:
        import truststore

        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:
        log.exception("system certificate store unavailable; using Python's default")
        return ssl.create_default_context()


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
            with connect(
                self.uri, ssl=tls_context(self.uri), open_timeout=5, compression=None, max_size=2**20, ping_interval=20
            ) as ws:
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

    def retarget(self, server: str, token: str) -> None:
        """Talk to a different PC from now on (the next ensure() connects there)."""
        self.server = server
        self.uri = ws_uri(server, token)
        self.last_error = ""
        self.close()


def make_inserter(cfg: Config) -> Any:
    c = cfg.client
    if sys.platform == "win32":
        from dictate.insert import TextInserter

        return TextInserter(restore_clipboard=c.restore_clipboard, restore_delay_ms=c.restore_delay_ms)
    if sys.platform == "darwin":
        from dictate.insert_mac import MacInserter

        return MacInserter(restore_clipboard=c.restore_clipboard, restore_delay_ms=c.restore_delay_ms)
    raise SystemExit("The dictate client supports Windows and macOS.")


MAC_SETTINGS = {
    "Accessibility": "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
    "Input Monitoring": "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent",
}


def mac_permissions(*, prompt: bool) -> dict[str, bool]:
    """Accessibility (to press Cmd+V) and Input Monitoring (to see the hotkey). With prompt=True,
    macOS shows its own permission prompts for whatever is missing."""
    got = {"Accessibility": True, "Input Monitoring": True}
    try:
        from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt

        got["Accessibility"] = bool(AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: prompt}))
    except Exception:
        log.debug("accessibility check unavailable", exc_info=True)
    try:
        import Quartz

        got["Input Monitoring"] = bool(Quartz.CGPreflightListenEventAccess())
        if prompt and not got["Input Monitoring"]:
            Quartz.CGRequestListenEventAccess()
    except Exception:
        log.debug("input monitoring check unavailable", exc_info=True)
    return got


def mac_permissions_ok() -> bool:
    return all(mac_permissions(prompt=True).values())


class Client:
    """The hotkey client. It also implements tray.Controls: everything the menu can change."""

    def __init__(
        self,
        cfg: Config,
        *,
        token: str | None = None,
        server: str | None = None,
        mode: str = "client",
        app: bool = False,
        label: str = "",
        reconfigure: Callable[[], None] | None = None,
    ) -> None:
        c = cfg.client
        self.cfg = cfg
        self.mode = mode  # "client", or "pc" when this computer also runs the server
        self.app = app  # the installed app: there's no terminal, so problems are shown in dialogs
        self.label = label  # where the speech recognition runs, e.g. "Online: OpenAI + Claude"
        self.language = c.language
        self.hotkey = c.hotkey
        self.enabled = True
        self._reconfigure = reconfigure
        self.can_reconfigure = reconfigure is not None  # the app: "Change how Dictate works…"
        self.can_change_server = mode == "client" and reconfigure is None and c.mode == "remote"
        self.inserter = make_inserter(cfg)
        self.sounds = Sounds(paths.data_dir() / "sounds", enabled=c.sounds, volume=c.volume)
        self.server = server or c.server
        self.conn = Connection(self.server, token if token is not None else c.token, self._connection_changed)
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
        self._blocked = ""  # shown instead of "Ready" while macOS permissions are missing

    # -- state / tray ---------------------------------------------------------------------------
    def _ready_status(self) -> str:
        return f"Ready: hold {display_key(self.hotkey)} and speak"

    def _set_state(self, state: str, status: str) -> None:
        if state == "ready" and self._blocked:
            state, status = "offline", self._blocked
        self._state = (state, status)
        if not self.enabled and state != "off":
            return  # turned off: the icon keeps saying so
        if self.tray is not None:
            self.tray.set_state(state, status)

    def _ready_or_current(self) -> tuple[str, str]:
        return ("ready", self._ready_status()) if self.conn.connected else self._state

    def _connection_changed(self, ok: bool, error: str) -> None:
        if ok:
            log.info("connected to %s", self.conn.server)
            self._set_state("ready", self._ready_status())
        else:
            log.warning("offline: %s", error)
            self._set_state("offline", f"Offline: {error}")

    def _keep_connected(self) -> None:
        while not self._stopping.wait(5):
            if self.enabled and not self.conn.connected:
                self.conn.ensure()

    # -- hotkey thread (never blocks) -----------------------------------------------------------
    def on_event(self, ev: Event) -> None:
        if ev.action == Action.START:
            if sys.platform == "win32" and self.hotkey.startswith("alt"):
                with contextlib.suppress(Exception):
                    from dictate.platform_win import mask_alt_menu

                    mask_alt_menu()  # releasing a lone Alt would otherwise open the app's menu bar
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
            self._set_state(*self._ready_or_current())

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
            message = str(m.get("message", "")).removeprefix("server error: ")
            log.warning("dictation failed: %s", message)
            self.sounds.play("error")
            state, status = self._ready_or_current()
            if state == "ready" and message:  # e.g. the online service rejected the key: say so in the menu
                status = f"Didn't work: {message[:150]}"
            self._set_state(state, status)
            return
        text = m.get("text", "")
        if not text:
            self.sounds.play("nothing")
        else:
            self.inserter.insert(text)
        what = f"{len(text.split())} words" if text else "nothing heard"
        shown = f": {text}" if self.cfg.logging.log_transcripts else ""
        log.info("%s in %.0f ms (server %s ms, %s)%s", what, rtt, m.get("ms"), m.get("mode"), shown)
        self._set_state("ready", self._ready_status())

    def _check_level(self) -> None:
        if self._level_checked or not self._level_buf:
            return
        self._level_checked = True
        warning = level_warning(b"".join(self._level_buf))
        self._level_buf = []
        if warning:
            log.warning(warning)

    # -- menu actions (tray.Controls; called from a worker thread, may block) ------------------------
    def _save(self, key: str, value: str | bool) -> None:
        if self.cfg.source_path is None:
            return
        try:
            set_value(self.cfg.source_path, "client", key, value)
        except OSError:
            log.exception("could not save the setting %s", key)

    def _tell(self, title: str, text: str) -> None:
        log.warning("%s: %s", title, text)
        if self.app:
            from dictate import dialogs

            dialogs.message(title, text)

    def set_language(self, code: str) -> None:
        self.language = code
        self._save("language", code)
        log.info("language: %s", code)

    def set_enabled(self, on: bool) -> None:
        """Off: the hotkey does nothing, the microphone is released (other apps and the system's own
        dictation can use it) and the connection to the PC is closed. Quitting does the same."""
        if on == self.enabled:
            return
        self.enabled = on
        self.hotkeys.set_paused(not on)
        if on:
            log.info("turned on")
            try:
                self.mic.open()
            except Exception:
                log.exception("could not open the microphone")
            self._set_state("offline", "Connecting…")
            self.conn.ensure()
        else:
            log.info("turned off")
            self.mic.end()
            self.mic.close()
            self.conn.close()
            self._set_state("off", "Off: Dictate isn't using the hotkey or the microphone")

    @property
    def sounds_on(self) -> bool:
        return self.sounds.enabled

    def set_sounds(self, on: bool) -> None:
        self.sounds.enabled = on
        self._save("sounds", on)

    def set_hotkey(self, name: str) -> None:
        if name == self.cfg.client.cancel:
            return
        self.hotkey = name
        self.hotkeys.set_ptt(name)
        self._save("hotkey", name)
        log.info("hotkey: %s", name)
        self._set_state(*self._ready_or_current())

    def microphones(self) -> list[str]:
        try:
            from dictate.mic import microphone_names

            return microphone_names()
        except Exception:
            log.debug("could not list microphones", exc_info=True)
            return []

    def microphone(self) -> str:
        return str(self.cfg.client.input_device)

    def set_microphone(self, name: str) -> None:
        try:
            self.mic.set_device(name)
        except Exception as e:
            self._tell("Microphone not available", f"Dictate couldn't use “{name}” ({e}). Is it plugged in?")
            return
        self.cfg.client.input_device = name
        self._save("input_device", name)
        log.info("microphone: %s", self.mic.name)

    def autostart_on(self) -> bool:
        from dictate import autostart

        try:
            return autostart.is_enabled()
        except Exception:
            return False

    def set_autostart(self, on: bool) -> None:
        from dictate import autostart

        try:
            log.info(autostart.enable(self.mode) if on else autostart.disable())
        except Exception as e:
            log.exception("autostart")
            self._tell("Couldn't change that", f"Dictate couldn't change the start-at-login setting ({e}).")

    def change_server(self) -> None:
        from dictate import offline
        from dictate.app import ask_for_link

        current = urlsplit(self.conn.server).hostname or ""
        got = ask_for_link(allow_host=lambda host: offline.install_network_guard(allow_hosts=[current, host]))
        server = got[0] if got is not None else self.conn.server
        offline.install_network_guard(allow_hosts=[urlsplit(server).hostname or ""])
        if got is None:
            return
        server, token = got
        self.cfg.client.server, self.cfg.client.token = server, token
        self._save("server", server)
        self._save("token", token)
        log.info("now using %s", server)
        self.conn.retarget(server, token)
        self._set_state("offline", "Connecting…")
        if self.enabled:
            self.conn.ensure()

    def reconfigure(self) -> None:
        if self._reconfigure is not None:
            self._reconfigure()

    def quit(self) -> None:
        """Stop everything, including the tray icon (which ends run())."""
        if self.tray is not None:
            self.tray.quit()
        else:
            self.stop()

    def open_settings(self) -> None:
        path = self.cfg.source_path
        if path is None:
            return
        if sys.platform == "win32":
            os.startfile(path)
        else:
            subprocess.Popen(["open", "-t", str(path)])

    # -- macOS permissions (installed app) ---------------------------------------------------------------
    def _wait_for_permission(self) -> None:
        """Explain what to switch on, then keep checking: Dictate starts working the moment it's allowed,
        without a restart (unless macOS itself asks for one)."""
        from dictate import dialogs

        missing = [name for name, ok in mac_permissions(prompt=False).items() if not ok] or ["Input Monitoring"]
        choice = dialogs.message(
            "Dictate needs your permission",
            "To hear the hotkey and type for you, Dictate needs two switches in System Settings › "
            "Privacy & Security:\n\n"
            "•  Accessibility: turn on Dictate\n"
            "•  Input Monitoring: turn on Dictate\n\n"
            "If Dictate isn't in a list, click + and pick it from Applications. If macOS offers to "
            "“Quit & Reopen” Dictate, click it.",
            ("Later", "Open System Settings"),
        )
        guided = choice == "Open System Settings"
        opened: set[str] = set()
        if guided:
            subprocess.Popen(["open", MAC_SETTINGS[missing[0]]])
            opened.add(missing[0])
        while not self._stopping.wait(2):
            got = mac_permissions(prompt=False)
            if not self.hotkeys.started:
                with contextlib.suppress(PermissionError):
                    self.hotkeys.start()
            if got["Accessibility"] and self.hotkeys.started:
                self._blocked = ""
                log.info("macOS permissions granted")
                self._set_state(*self._ready_or_current())
                return
            if guided and got["Accessibility"] and "Input Monitoring" not in opened:
                subprocess.Popen(["open", MAC_SETTINGS["Input Monitoring"]])  # on to the second switch
                opened.add("Input Monitoring")

    # -- lifecycle --------------------------------------------------------------------------------------
    def run(self, *, tray: bool = True, on_ready: Callable[[], None] | None = None) -> None:
        mac = sys.platform == "darwin"
        permitted = mac_permissions_ok() if mac else True
        if not permitted and not self.app:
            print("  macOS needs permission: System Settings > Privacy & Security > Accessibility (and Input")
            print("  Monitoring): turn on the app you started dictate from (Terminal), then restart dictate.")
        try:
            self.mic.open()
        except Exception:
            if not self.app:
                raise
            log.exception("could not open the chosen microphone: using the system's default")
            with contextlib.suppress(Exception):
                self.mic.set_device("")
        log.info("microphone: %s", self.mic.name)
        threading.Thread(target=self._worker, name="worker", daemon=True).start()
        try:
            self.hotkeys.start()
        except PermissionError as e:
            if not self.app:
                self.mic.close()
                raise SystemExit(f"\n  {e}\n") from None
            permitted = False
        if not permitted and self.app:
            self._blocked = "Needs permission: see System Settings › Privacy & Security"
            self._state = ("offline", self._blocked)

        def ready() -> None:  # once the icon is up: the caller's welcome, then any permission help
            if on_ready is not None:
                on_ready()
            if self._blocked:
                self._wait_for_permission()

        threading.Thread(target=self.conn.ensure, name="connect", daemon=True).start()
        threading.Thread(target=self._keep_connected, name="reconnect", daemon=True).start()
        c = self.cfg.client
        key, cancel = display_key(c.hotkey), display_key(c.cancel)
        print(f"\n  Ready. Hold {key} and speak; release to insert.")
        if c.hands_free:
            print(f"  Double-tap {key} for hands-free; tap it again to stop. {cancel} cancels.")
        print(f"  Speech recognition: {self.label or self.server}")
        if tray:
            from dictate.tray import Tray

            self.tray = Tray(self)
            self.tray.set_state(*self._state)
            print("  Turn it off, switch language or quit from the tray icon (or Ctrl+C here).\n")
            with contextlib.suppress(KeyboardInterrupt):
                self.tray.run(on_ready=ready)
        else:
            print("  Ctrl+C to quit.\n")
            threading.Thread(target=ready, daemon=True).start()
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
