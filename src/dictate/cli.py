"""Command line.

dictate              the client (Mac or PC): hotkey -> speak -> text at your cursor
dictate app          the same, as the installed app runs it (setup and messages in windows)
dictate pc           the PC with the GPU: server + client in one tray app
dictate server       the server only (no hotkey on this machine)
dictate setup        download the models (on the PC only)
dictate autostart    start dictate when you log in (on / off)
dictate notes        open the Notes window
dictate devices      list microphones
dictate config       show / edit the settings file
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from dictate import offline, paths
from dictate.config import ConfigError, ensure_config_file, ensure_server_token, load_config
from dictate.logs import setup_logging

log = logging.getLogger("dictate")


def _load(args: argparse.Namespace):
    try:
        return load_config(Path(args.config) if args.config else None)
    except ConfigError as e:
        raise SystemExit(f"Config error: {e}") from e


def _hold_hotkey():
    """Stop here if another Dictate already uses the hotkey on this computer (it would type everything twice)."""
    from dictate import instance

    lock = instance.hold("hotkey")
    if lock is None:
        raise SystemExit(
            "Dictate is already running on this computer (the installed app, or another `dictate pc` /\n"
            "`dictate client`), and two copies would type everything twice. Quit it first: right-click its\n"
            "tray icon (menu-bar icon on a Mac) > Quit Dictate. On the PC that runs the server, `dictate pc`\n"
            "does everything the app does, so you can uninstall the app there."
        )
    return lock


# -- client ---------------------------------------------------------------------------------------
def cmd_client(args: argparse.Namespace) -> None:
    cfg = _load(args)
    setup_logging(args.debug or cfg.logging.debug)
    lock = _hold_hotkey()  # noqa: F841 - held while the client runs
    from dictate import modes

    session = modes.start(cfg)  # remote: your PC's link; online: the engine runs here; local: the app's GPU engine
    if not session.token:
        log.warning("no [client] token set: copy it from the `dictate pc` output on your PC")
    offline.install_network_guard(allow_hosts=session.allow_hosts)  # only your server / your online services
    from dictate.client import Client

    try:
        client = Client(cfg, token=session.token, server=session.server, mode="client", label=session.label)
        client.run(tray=not args.no_tray)
    finally:
        session.close()


def cmd_app(args: argparse.Namespace) -> None:
    from dictate.app import main as app_main

    app_main()


# -- server ---------------------------------------------------------------------------------------
def _start_server(cfg) -> str:
    """Load models and start the WebSocket server in a background thread. Returns the token."""
    from dictate.engine import Engine
    from dictate.server import DictateServer, start_in_thread

    offline.enforce_offline_env()
    offline.install_network_guard()  # the server only ever talks to localhost
    token = ensure_server_token(cfg)
    engine = Engine(cfg)
    try:
        start_in_thread(DictateServer(engine, token), cfg.server.host, cfg.server.port)
    except BaseException as err:
        engine.close()
        if isinstance(err, OSError):
            raise SystemExit(f"Can't listen on port {cfg.server.port} ({err}). Is dictate already running?") from None
        raise SystemExit(f"Server failed to start: {err}") from None
    _print_connection_info(cfg, token)
    return token


def _print_connection_info(cfg, token: str) -> None:
    from dictate.server import tailscale_name

    if os.environ.get("DICTATE_QUIET"):  # the app's own background server: its output goes to a log file
        print(f"Server running on this PC (port {cfg.server.port}).")
        return

    port = cfg.server.port
    name = tailscale_name()
    print(f"\n  Server running on this PC (port {port}).")
    if name:
        print("  Your other devices reach it through Tailscale. If you haven't yet, run once on this PC:")
        print(f"      tailscale serve --bg {port}")
        print("  Your link (keep it private: it's the key to this PC's dictation):")
        print(f"      https://{name}/?t={token}")
        print("  Phone: open it in Safari (Chrome on Android), then Add to Home Screen.")
        print("  Mac / Windows: install the Dictate app and paste the link when it asks.")
        print(f'  (Or in a client settings file: server = "wss://{name}", token = "{token}")')
    else:
        print("  To use it from your Mac and iPhone: install Tailscale on all three devices")
        print("  (https://tailscale.com), sign in with the same account, then restart `dictate pc`.")
        print(f"  Token for your devices: {token}")
    print()


def cmd_pc(args: argparse.Namespace) -> None:
    cfg = _load(args)
    setup_logging(args.debug or cfg.logging.debug)
    lock = _hold_hotkey()  # noqa: F841 - checked before the models load; held while the client runs
    token = _start_server(cfg)
    from dictate.client import Client

    cfg.client.server = f"ws://127.0.0.1:{cfg.server.port}"  # this machine's client talks to its own server
    Client(cfg, token=token, mode="pc").run(tray=not args.no_tray)


def cmd_server(args: argparse.Namespace) -> None:
    import time

    cfg = _load(args)
    setup_logging(args.debug or cfg.logging.debug)
    _start_server(cfg)
    print("  Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("bye")


def cmd_setup(args: argparse.Namespace) -> None:
    cfg = _load(args)
    setup_logging(args.debug)
    from dictate.models import run_setup

    run_setup(cfg)


# -- small helpers ------------------------------------------------------------------------------------
def cmd_devices(args: argparse.Namespace) -> None:
    from dictate.mic import list_input_devices

    for idx, name, is_default in list_input_devices():
        print(f"{'*' if is_default else ' '} {idx:3d}  {name}")
    print("\n* = default. Set [client] input_device to a number or part of a name,")
    print('or to "computer audio" to transcribe what is playing on this computer instead.')


def cmd_config(args: argparse.Namespace) -> None:
    path = ensure_config_file(Path(args.config) if args.config else None)
    print(path)
    if args.edit:
        if sys.platform == "win32":
            os.startfile(path)
        else:
            import subprocess

            subprocess.Popen(["open", "-t", str(path)])


def cmd_autostart(args: argparse.Namespace) -> None:
    from dictate.autostart import disable, enable

    if args.state == "off":
        print(disable())
        return
    print(enable(args.mode or ("pc" if _has_models(args) else "client")))


def _has_models(args: argparse.Namespace) -> bool:
    try:
        from dictate import models

        return not models.missing_models(_load(args))
    except Exception:
        return False


def cmd_notes(args: argparse.Namespace) -> None:
    from dictate.notes.window import main as notes

    raise SystemExit(notes())


def cmd_bench(args: argparse.Namespace) -> None:  # developer tools (PC only)
    cfg = _load(args)
    setup_logging(args.debug)
    offline.enforce_offline_env()
    if args.bench_cmd == "record":
        from dictate.bench.record import record_session

        record_session(only=args.only, redo=args.redo, device=args.device)
    elif args.bench_cmd == "cleanup":
        from dictate.bench.cleanup import run_cleanup_bench
        from dictate.models import LLM_MODELS

        run_cleanup_bench(cfg, args.models or list(LLM_MODELS), with_clips=not args.no_clips)
    elif args.bench_cmd == "asr":
        from dictate.bench.asr import run_asr_bench

        run_asr_bench(cfg, variants=args.variants, repeat=args.repeat)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="dictate", description="Push-to-talk dictation with AI cleanup.")
    p.add_argument("--config", help=f"settings file (default: {paths.config_path()})")
    p.add_argument("--debug", action="store_true", help="verbose logging")
    sub = p.add_subparsers(dest="cmd")

    for name, help_ in (("client", "hotkey client (default)"), ("pc", "server + client on the PC with the GPU")):
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("--no-tray", action="store_true", help="no tray / menu-bar icon")
    ap = sub.add_parser("app", help="the client with setup and messages in windows (what the installed app runs)")
    ap.add_argument("--restarted", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--self-test", nargs="?", const="-", metavar="REPORT", help="check the app's pieces and exit")
    sub.add_parser("server", help="server only (speech recognition + AI cleanup)")
    sub.add_parser("setup", help="download the models (PC only; the only command that uses the internet)")
    a = sub.add_parser("autostart", help="start dictate when you log in")
    a.add_argument("state", choices=["on", "off"])
    a.add_argument("--mode", choices=["pc", "client", "server"], help="default: pc if models are installed")
    sub.add_parser("notes", help="open the Notes window")
    sub.add_parser("devices", help="list microphones")
    c = sub.add_parser("config", help="show the settings file")
    c.add_argument("--edit", action="store_true", help="open it in your editor")

    b = sub.add_parser("bench", help="developer benchmarks (PC)")
    bsub = b.add_subparsers(dest="bench_cmd", required=True)
    br = bsub.add_parser("record")
    br.add_argument("--only", nargs="*")
    br.add_argument("--redo", action="store_true")
    br.add_argument("--device", default="")
    ba = bsub.add_parser("asr")
    ba.add_argument("--variants", nargs="*", default=["cuda", "cpu-int8"])
    ba.add_argument("--repeat", type=int, default=3)
    bc = bsub.add_parser("cleanup")
    bc.add_argument("--models", nargs="*")
    bc.add_argument("--no-clips", action="store_true")

    args = p.parse_args(argv)
    if args.cmd is None:
        args.cmd, args.no_tray = "client", False
    handlers = {
        "client": cmd_client, "app": cmd_app, "pc": cmd_pc, "server": cmd_server, "setup": cmd_setup,
        "autostart": cmd_autostart, "notes": cmd_notes,
        "devices": cmd_devices, "config": cmd_config, "bench": cmd_bench,
    }  # fmt: skip
    handlers[args.cmd](args)


if __name__ == "__main__":
    main()
