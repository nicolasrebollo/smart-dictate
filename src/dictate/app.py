"""The installed desktop app (Dictate.exe on Windows, Dictate.app on Mac). No terminal anywhere.

First start: asks for the link from the PC (the same link the phone uses), checks it, saves it,
turns on start-at-login and says how to use Dictate. After that it just runs, with its tray /
menu-bar icon. Opening it again while it runs doesn't start a second copy: it says where the first is.
"""

from __future__ import annotations

import contextlib
import logging
import sys
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from dictate import __version__, dialogs, instance, paths
from dictate.invite import check_server, parse_invite

log = logging.getLogger("dictate")

MAC = sys.platform == "darwin"
ICON_WHERE = (
    "the microphone icon in the menu bar, at the top right of the screen. Click it"
    if MAC
    else "the microphone icon in the taskbar, bottom right (click ^ if you don't see it). Right-click it"
)


def ask_for_link(allow_host: Callable[[str], Any] | None = None) -> tuple[str, str] | None:
    """Ask for the link until it's a working one (or the person chooses to keep it anyway).
    Returns (server, token), or None if they cancelled. A link on the clipboard is filled in."""
    clip = dialogs.clipboard_text().strip()
    default = clip if parse_invite(clip) else ""
    prompt = (
        "Paste the link you were given for Dictate. It looks like\n"
        "https://name.something.ts.net/?t=…\n\n"
        "(It's the same link the phone app uses. Tailscale needs to be on.)"
    )
    while True:
        text = dialogs.ask("Set up Dictate", prompt, default, ok="Connect")
        if text is None:
            return None
        got = parse_invite(text)
        if got is None:
            dialogs.message(
                "That isn't a Dictate link",
                "The link starts with https:// and has ?t= followed by a code in it. "
                "Copy the whole link and paste it again.",
            )
            default = text
            continue
        if allow_host is not None:
            allow_host(urlsplit(got[0]).hostname or "")
        problem = check_server(*got)
        if not problem:
            return got
        choice = dialogs.message(
            "Couldn't connect",
            problem + "\n\nYou can keep the link anyway: Dictate keeps trying in the background.",
            ("Keep it anyway", "Try again"),
        )
        if choice == "Keep it anyway":
            return got
        if choice is None:
            return None
        default = text


def configured(cfg: Any) -> bool:
    host = urlsplit(cfg.client.server).hostname or ""
    return bool(cfg.client.token) and host not in ("127.0.0.1", "localhost", "::1", "")


def _mac_not_in_applications() -> bool:
    """Run straight from the download (the disk image, or a quarantined copy macOS moved aside)."""
    exe = sys.executable
    return exe.startswith("/Volumes/") or "/AppTranslocation/" in exe


def _welcome(cfg: Any) -> None:
    from dictate.hotkeys import display_key

    key = display_key(cfg.client.hotkey)
    dialogs.message(
        "Dictate is ready",
        f"Hold {key}, speak, and let go: your words are typed wherever your cursor is.\n\n"
        f"Dictate lives in {ICON_WHERE} to turn Dictate off or on, change the language, hotkey or "
        "microphone, or quit.\n\n"
        "It starts by itself when you log in. You can change that in the same menu.",
    )


def main() -> None:
    if sys.platform == "win32":
        with contextlib.suppress(Exception):  # sharp dialogs on high-DPI screens
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
    from dictate.logs import setup_logging

    setup_logging()
    log.info("Dictate %s starting", __version__)

    if MAC and _mac_not_in_applications():
        dialogs.message(
            "Move Dictate to Applications first",
            "Dictate is running from the download. Drag the Dictate icon onto the Applications folder, "
            "then open Dictate from Applications (or Launchpad).",
        )
        return
    lock = instance.hold("app")
    if lock is None:
        dialogs.message("Dictate is already running", f"Look for {ICON_WHERE} for the menu.")
        return
    hotkey_lock = instance.hold("hotkey")
    if hotkey_lock is None:  # `dictate pc` / `dictate client` from a terminal: both would type everything twice
        dialogs.message(
            "Dictate is already running on this computer",
            "Another copy of Dictate (started from a terminal, e.g. the PC that runs the server) already "
            "uses the hotkey here, so this one would type everything twice.\n\n"
            "On the PC that runs the server you don't need the app: `dictate pc` already does the same. "
            "To use the app instead, quit the other copy first (Quit Dictate in its menu).",
        )
        return

    from dictate import autostart, offline
    from dictate.config import ConfigError, load_config, set_value

    try:
        cfg = load_config()
    except ConfigError as e:
        choice = dialogs.message(
            "Dictate's settings have a problem",
            f"{e}\n\nStart over with fresh settings? You'll paste your link again.",
            ("Quit", "Start over"),
        )
        if choice != "Start over":
            return
        path = paths.config_path()
        path.replace(path.with_name("config.broken.toml"))
        cfg = load_config()

    first = not configured(cfg)
    if first:
        got = ask_for_link()
        if got is None:
            return
        assert cfg.source_path is not None
        set_value(cfg.source_path, "client", "server", got[0])
        set_value(cfg.source_path, "client", "token", got[1])
        cfg = load_config()
        try:
            autostart.enable("client")
        except Exception:
            log.exception("could not turn on start-at-login")

    offline.install_network_guard(allow_hosts=[urlsplit(cfg.client.server).hostname or ""])
    from dictate.client import Client

    client = Client(cfg, token=cfg.client.token, app=True)

    def ready() -> None:
        if first:
            _welcome(cfg)
            if MAC:
                client.mic.request_access()  # macOS asks for the microphone now, not mid-dictation

    try:
        client.run(tray=True, on_ready=ready)
    except Exception as e:
        log.exception("Dictate stopped")
        dialogs.message("Dictate stopped", f"Something went wrong ({e}). Details are in {paths.log_dir()}.")
