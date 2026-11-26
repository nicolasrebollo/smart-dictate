"""Start dictate when you log in (Windows Startup folder / macOS LaunchAgent). No console window."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

LABEL = "com.dictate.app"


def _python_no_console() -> str:
    exe = Path(sys.executable)
    if sys.platform == "win32":
        pythonw = exe.with_name("pythonw.exe")
        return str(pythonw if pythonw.exists() else exe)
    return str(exe)


def _windows_script() -> Path:
    appdata = Path(os.environ["APPDATA"])
    return appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "dictate.vbs"


def _mac_plist() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def enable(mode: str) -> str:
    args = [_python_no_console(), "-m", "dictate.cli", mode]
    if sys.platform == "win32":
        path = _windows_script()
        command = " ".join(f'""{a}""' if " " in a else a for a in args)
        path.write_text(f'CreateObject("WScript.Shell").Run "{command}", 0, False\n', encoding="utf-8")
        return f"dictate ({mode}) will start when you log in. (Startup item: {path})"
    if sys.platform == "darwin":
        path = _mac_plist()
        path.parent.mkdir(parents=True, exist_ok=True)
        log = Path.home() / "Library" / "Logs" / "dictate.log"
        items = "".join(f"<string>{a}</string>" for a in args)
        path.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            f'<plist version="1.0"><dict><key>Label</key><string>{LABEL}</string>'
            f"<key>ProgramArguments</key><array>{items}</array>"
            "<key>RunAtLoad</key><true/><key>ProcessType</key><string>Interactive</string>"
            f"<key>StandardOutPath</key><string>{log}</string><key>StandardErrorPath</key><string>{log}</string>"
            "</dict></plist>\n",
            encoding="utf-8",
        )
        subprocess.run(["launchctl", "load", "-w", str(path)], capture_output=True, check=False)
        return (
            f"dictate ({mode}) will start when you log in. macOS will ask for Microphone and Accessibility "
            f"permission for Python the first time. (LaunchAgent: {path})"
        )
    raise SystemExit("autostart supports Windows and macOS")


def disable() -> str:
    if sys.platform == "win32":
        path = _windows_script()
    elif sys.platform == "darwin":
        path = _mac_plist()
        subprocess.run(["launchctl", "unload", "-w", str(path)], capture_output=True, check=False)
    else:
        raise SystemExit("autostart supports Windows and macOS")
    if path.exists():
        path.unlink()
        return "dictate won't start at login anymore."
    return "autostart was not on."
