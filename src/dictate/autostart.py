"""Start dictate when you log in. No console window.

Windows: a value under HKCU\\...\\CurrentVersion\\Run (it shows in Task Manager > Startup apps).
macOS:   a LaunchAgent in ~/Library/LaunchAgents (it shows in System Settings > General > Login Items).

The installed app (Dictate.exe / Dictate.app) starts itself; a source checkout starts
`python -m dictate.cli <mode>`. Turning it on or off never starts or stops the running copy.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

LABEL = "com.dictate.app"
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_VALUE = "Dictate"


def frozen() -> bool:
    """Running as the installed app (built with PyInstaller), not from source."""
    return bool(getattr(sys, "frozen", False))


def launch_command(mode: str) -> list[str]:
    if frozen():
        return [sys.executable]
    exe = Path(sys.executable)
    if sys.platform == "win32" and (pythonw := exe.with_name("pythonw.exe")).exists():
        exe = pythonw  # no console window
    return [str(exe), "-m", "dictate.cli", mode]


def _windows_command_line(args: list[str]) -> str:
    return subprocess.list2cmdline(args)


def _legacy_windows_script() -> Path:  # older versions used a .vbs in the Startup folder
    appdata = Path(os.environ.get("APPDATA", ""))
    return appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "dictate.vbs"


def _mac_plist() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def mac_plist_text(args: list[str]) -> str:
    log = Path.home() / "Library" / "Logs" / "dictate.log"
    items = "".join(f"<string>{escape(a)}</string>" for a in args)
    bundle = "<key>AssociatedBundleIdentifiers</key><string>com.dictate.app</string>" if frozen() else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        f'<plist version="1.0"><dict><key>Label</key><string>{LABEL}</string>'
        f"<key>ProgramArguments</key><array>{items}</array>{bundle}"
        "<key>RunAtLoad</key><true/><key>ProcessType</key><string>Interactive</string>"
        f"<key>StandardOutPath</key><string>{escape(str(log))}</string>"
        f"<key>StandardErrorPath</key><string>{escape(str(log))}</string>"
        "</dict></plist>\n"
    )


def is_enabled() -> bool:
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
                winreg.QueryValueEx(key, _RUN_VALUE)
            return True
        except OSError:
            return _legacy_windows_script().exists()
    if sys.platform == "darwin":
        return _mac_plist().exists()
    return False


def enable(mode: str = "client") -> str:
    args = launch_command(mode)
    if sys.platform == "win32":
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            winreg.SetValueEx(key, _RUN_VALUE, 0, winreg.REG_SZ, _windows_command_line(args))
        _legacy_windows_script().unlink(missing_ok=True)
        return "dictate will start when you log in (Task Manager > Startup apps lists it)."
    if sys.platform == "darwin":
        path = _mac_plist()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(mac_plist_text(args), encoding="utf-8")
        # clear a "disabled" mark left by an older `launchctl unload -w` (this doesn't start it now)
        subprocess.run(["launchctl", "enable", f"gui/{os.getuid()}/{LABEL}"], capture_output=True, check=False)
        return "dictate will start when you log in (System Settings > General > Login Items lists it)."
    raise SystemExit("autostart supports Windows and macOS")


def disable() -> str:
    was = is_enabled()
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, _RUN_VALUE)
        except OSError:
            pass
        _legacy_windows_script().unlink(missing_ok=True)
    elif sys.platform == "darwin":
        _mac_plist().unlink(missing_ok=True)  # not unloaded: that would quit the running copy
    else:
        raise SystemExit("autostart supports Windows and macOS")
    return "dictate won't start at login anymore." if was else "autostart was not on."
