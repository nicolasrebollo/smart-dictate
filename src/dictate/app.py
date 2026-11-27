"""The installed desktop app (Dictate.exe on Windows, Dictate.app on Mac). No terminal anywhere.

First start: asks where the speech recognition should run (see modes.py), sets that up, turns on
start-at-login and says how to use Dictate:

  - a PC that shares Dictate with you: paste its link (the same link the phone uses)
  - this computer's NVIDIA graphics card (Windows): downloads the engine and models once (~6 GB)
  - an online AI service (OpenAI, Claude, Gemini, Grok): paste an API key

After that it just runs, with its tray / menu-bar icon; "Change how Dictate works…" in the menu goes
through the same steps again. Opening it again while it runs doesn't start a second copy.
"""

from __future__ import annotations

import contextlib
import logging
import subprocess
import sys
import time
import webbrowser
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from dictate import __version__, dialogs, instance, paths
from dictate.invite import check_server, parse_invite

log = logging.getLogger("dictate")

MAC = sys.platform == "darwin"
KEYCHAIN = "Keychain" if MAC else "Windows Credential Manager"
ICON_WHERE = (
    "the microphone icon in the menu bar, at the top right of the screen. Click it"
    if MAC
    else "the microphone icon in the taskbar, bottom right (click ^ if you don't see it). Right-click it"
)


# -- a PC's link --------------------------------------------------------------------------------------
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


def _setup_remote(cfg: Any) -> bool:
    from dictate.config import set_value

    got = ask_for_link()
    if got is None:
        return False
    path = cfg.source_path
    set_value(path, "client", "server", got[0])
    set_value(path, "client", "token", got[1])
    set_value(path, "client", "mode", "remote")
    return True


# -- an online AI service -----------------------------------------------------------------------------
def ask_for_key(provider: Any) -> tuple[str, list[str]] | None:
    """Ask for an API key until the service accepts it. Returns (key, the models it can use)."""
    from dictate.cloud.http import HTTPError
    from dictate.cloud.providers import KeyRejected, list_models, looks_like_key

    default = looks_like_key(provider, dialogs.clipboard_text())
    if not default:
        choice = dialogs.message(
            f"Your {provider.name} API key",
            f"Dictate needs an API key from {provider.company}. You create one on their website (it needs "
            "an account with a payment method or credit), copy it, and paste it here.\n\n"
            f"{provider.key_url}",
            ("Cancel", "Open the website", "I have my key"),
        )
        if choice is None or choice == "Cancel":
            return None
        if choice == "Open the website":
            webbrowser.open(provider.key_url)
    prompt = (
        f"Paste your {provider.name} API key.\n\n"
        f"It's kept in this computer's {KEYCHAIN}, not in a file, and only ever sent to {provider.company}."
    )
    while True:
        key = dialogs.ask(f"Your {provider.name} API key", prompt, default, ok="Check key", secret=True)
        if key is None:
            return None
        key = key.strip()
        if not key:
            continue
        try:
            return key, list_models(provider, key)
        except KeyRejected as e:
            choice = dialogs.message("That key didn't work", f"{e}\n\nCopy the whole key and try again.",
                                     ("Cancel", "Try again"))  # fmt: skip
        except HTTPError as e:
            choice = dialogs.message(f"Couldn't check the key with {provider.name}", f"{e.message}",
                                     ("Cancel", "Try again"))  # fmt: skip
        if choice != "Try again":
            return None
        default = ""


def _setup_online(cfg: Any) -> bool:
    from dictate.cloud.keys import set_key
    from dictate.cloud.providers import PROVIDERS, choose_models
    from dictate.config import set_value

    cleanup_id = dialogs.choose(
        "Use an online AI service",
        "Which service do you have (or want) an API key for?\n\n"
        "Dictate sends your recordings and text to it: it turns your speech into text and tidies it up "
        "(punctuation, filler words, numbers). You pay the service for what you use, usually a few cents "
        "per hour of dictation.",
        [(p.id, p.company) for p in PROVIDERS.values()],
    )
    if cleanup_id is None:
        return False
    cleanup = PROVIDERS[cleanup_id]
    got = ask_for_key(cleanup)
    if got is None:
        return False
    keys = {cleanup.id: got[0]}
    speech_model, cleanup_model = choose_models(cleanup, got[1])
    speech = cleanup
    if not cleanup.does_speech:
        speech_id = dialogs.choose(
            "Which service should hear you?",
            f"{cleanup.name} works with text, not audio, so a second service turns your speech into text. "
            f"{cleanup.name} then tidies it up.",
            [(p.id, p.company) for p in PROVIDERS.values() if p.does_speech],
        )
        if speech_id is None:
            return False
        speech = PROVIDERS[speech_id]
        got2 = ask_for_key(speech)
        if got2 is None:
            return False
        keys[speech.id] = got2[0]
        speech_model = choose_models(speech, got2[1])[0]
    if not cleanup_model:
        dialogs.message(
            "No tidy-up model found",
            f"Your {cleanup.name} key can't use any of the text models Dictate knows, so Dictate will tidy "
            "your text with its own rules (punctuation, numbers, spoken commands) instead.",
        )
    for pid, key in keys.items():
        set_key(pid, key)
    path = cfg.source_path
    set_value(path, "online", "speech", speech.id)
    set_value(path, "online", "speech_model", speech_model)
    set_value(path, "online", "cleanup", cleanup.id if cleanup_model else "")
    set_value(path, "online", "cleanup_model", cleanup_model)
    set_value(path, "client", "mode", "online")
    log.info("online mode: speech %s (%s), cleanup %s (%s)", speech.id, speech_model, cleanup.id, cleanup_model)
    return True


# -- this computer's graphics card --------------------------------------------------------------------
def _install_local(title: str) -> bool:
    from dictate import local_gpu

    try:
        dialogs.progress(title, local_gpu.install)
        return True
    except Exception as e:
        log.exception("local setup failed")
        dialogs.message(
            "Setup didn't finish",
            f"{e}\n\nCheck the internet connection and try again (it picks up where it stopped). "
            f"Details are in {paths.log_dir() / 'local-setup.log'}.",
        )
        return False


def _setup_local(cfg: Any) -> bool:
    from dictate import local_gpu
    from dictate.config import set_value

    gpu = local_gpu.available()
    if gpu is None:
        return False
    name, mb = gpu
    if mb < local_gpu.MIN_VRAM_MB:
        choice = dialogs.message(
            "Your graphics card may be too small",
            f"{name} has {mb / 1024:.0f} GB of memory; Dictate needs about {local_gpu.MIN_VRAM_MB / 1000:.0f} GB. "
            "It may be slow or not start.",
            ("Cancel", "Try anyway"),
        )
        if choice != "Try anyway":
            return False
    choice = dialogs.message(
        "Download the speech engine",
        f"Dictate will download about 6 GB (Python, NVIDIA's GPU libraries, the speech recognition and the "
        f"AI cleanup models) and keep them in {local_gpu.runtime_dir()}.\n\n"
        "It takes 10 to 30 minutes, depending on your connection. After that Dictate works without the "
        "internet, and nothing you say leaves this computer.",
        ("Cancel", "Download"),
    )
    if choice != "Download":
        return False
    if not _install_local("Setting up Dictate"):
        return False
    set_value(cfg.source_path, "client", "mode", "local")
    return True


# -- choosing ---------------------------------------------------------------------------------------
def choose_mode(cfg: Any) -> bool:
    """Ask where the speech recognition should run and set it up. True if something was set up."""
    from dictate import local_gpu

    options = [("remote", "A PC that shares Dictate with me (I have a link)")]
    gpu = local_gpu.available()
    if gpu is not None:
        options.append(("local", f"This computer's graphics card ({gpu[0]}): free and private, a 6 GB download"))
    options.append(("online", "An online AI service (OpenAI, Claude, Gemini or Grok), with my own API key"))
    while True:
        mode = dialogs.choose(
            "Set up Dictate",
            "Where should your speech be turned into text?",
            options,
        )
        if mode is None:
            return False
        done = {"remote": _setup_remote, "local": _setup_local, "online": _setup_online}[mode](cfg)
        if done:
            return True


def configured(cfg: Any) -> bool:
    mode = cfg.client.mode
    if mode == "online":
        from dictate.cloud.keys import get_key

        needed = [p for p in (cfg.online.speech, cfg.online.cleanup) if p]
        return bool(cfg.online.speech) and all(get_key(p) for p in needed)
    if mode == "local":
        return True  # installed (or updated) on start, see main()
    host = urlsplit(cfg.client.server).hostname or ""
    return bool(cfg.client.token) and host not in ("127.0.0.1", "localhost", "::1", "")


def relaunch() -> None:
    """Start a fresh copy (it waits for this one to exit), e.g. after the setup changed."""
    args = [sys.executable, "--restarted"] if getattr(sys, "frozen", False) else [
        sys.executable, "-m", "dictate.cli", "app", "--restarted"]  # fmt: skip
    flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS if sys.platform == "win32" else 0
    subprocess.Popen(args, close_fds=True, creationflags=flags)


# -- running ----------------------------------------------------------------------------------------
def _mac_not_in_applications() -> bool:
    """Run straight from the download (the disk image, or a quarantined copy macOS moved aside)."""
    exe = sys.executable
    return exe.startswith("/Volumes/") or "/AppTranslocation/" in exe


def _welcome(cfg: Any, label: str) -> None:
    from dictate.hotkeys import display_key

    key = display_key(cfg.client.hotkey)
    where = {
        "online": f"Your speech goes to {label.removeprefix('Online: ')}.",
        "local": "Everything runs on this computer.",
    }.get(cfg.client.mode, "")
    dialogs.message(
        "Dictate is ready",
        f"Hold {key}, speak, and let go: your words are typed wherever your cursor is. {where}\n\n"
        f"Dictate lives in {ICON_WHERE} to turn Dictate off or on, change the language, hotkey or "
        "microphone, change how Dictate works, or quit.\n\n"
        "It starts by itself when you log in. You can change that in the same menu.",
    )


def _hold_app_lock(restarted: bool) -> Any:
    """The single-instance lock; after a relaunch, wait for the previous copy to finish exiting."""
    deadline = time.monotonic() + (20 if restarted else 0)
    while True:
        lock = instance.hold("app")
        if lock is not None or time.monotonic() >= deadline:
            return lock
        time.sleep(0.3)


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    restarted = "--restarted" in argv
    if test := next((a for a in argv if a.startswith("--self-test")), None):
        from dictate import selftest

        raise SystemExit(selftest.run(test.partition("=")[2]))
    if "--notes" in argv:  # the Notes window, started from the menu (see notes/window.py)
        from dictate.notes.window import main as notes

        raise SystemExit(notes())
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
    lock = _hold_app_lock(restarted)
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

    from dictate import autostart, local_gpu, modes, offline
    from dictate.config import ConfigError, load_config

    try:
        cfg = load_config()
    except ConfigError as e:
        choice = dialogs.message(
            "Dictate's settings have a problem",
            f"{e}\n\nStart over with fresh settings? You'll set Dictate up again.",
            ("Quit", "Start over"),
        )
        if choice != "Start over":
            return
        path = paths.config_path()
        path.replace(path.with_name("config.broken.toml"))
        cfg = load_config()

    first = not configured(cfg)
    if first:
        if not choose_mode(cfg):
            return
        cfg = load_config()
        try:
            autostart.enable("client")
        except Exception:
            log.exception("could not turn on start-at-login")
    elif cfg.client.mode == "local" and not local_gpu.installed() and not _install_local("Updating Dictate"):
        return  # (a new version of the app brings its engine up to date first)

    try:
        session = modes.start(cfg)
    except Exception as e:
        log.exception("couldn't start")
        choice = dialogs.message(
            "Dictate couldn't start",
            f"{e}\n\nYou can set Dictate up differently, or quit and try again later.",
            ("Quit", "Change how Dictate works"),
        )
        if choice == "Change how Dictate works" and choose_mode(cfg):
            del lock, hotkey_lock
            relaunch()
        return

    offline.install_network_guard(allow_hosts=session.allow_hosts)
    from dictate.client import Client

    client: Client

    def reconfigure() -> None:
        """Menu: Change how Dictate works… (restarts Dictate with the new setup)."""
        offline.uninstall_network_guard()  # the new service / PC isn't on the list yet
        try:
            changed = choose_mode(load_config())
        finally:
            offline.install_network_guard(allow_hosts=session.allow_hosts)
        if changed:
            relaunch()
            client.quit()

    client = Client(cfg, server=session.server, token=session.token, app=True, label=session.label,
                    reconfigure=reconfigure)  # fmt: skip

    def ready() -> None:
        if first:
            _welcome(cfg, session.label)
            if MAC:
                client.mic.request_access()  # macOS asks for the microphone now, not mid-dictation

    try:
        client.run(tray=True, on_ready=ready)
    except Exception as e:
        log.exception("Dictate stopped")
        dialogs.message("Dictate stopped", f"Something went wrong ({e}). Details are in {paths.log_dir()}.")
    finally:
        session.close()
