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

# The installed app and a source checkout get separate entries, so installing, uninstalling or
# toggling one never touches the other's (e.g. the app on the PC that runs `dictate pc`).
LABEL = "com.dictate.app"  # the installed app (also its bundle id)
SOURCE_LABEL = "com.dictate.source"
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_VALUE = "Dictate"
_SOURCE_RUN_VALUE = "Dictate (from source)"
_SOURCE_MARK = "dictate.cli"  # in every command a source checkout registers


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


def _mac_plist(label: str | None = None) -> Path:
    label = label or (LABEL if frozen() else SOURCE_LABEL)
    return Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"


def mac_plist_text(args: list[str], label: str | None = None) -> str:
    label = label or (LABEL if frozen() else SOURCE_LABEL)
    log = Path.home() / "Library" / "Logs" / "dictate.log"
    items = "".join(f"<string>{escape(a)}</string>" for a in args)
    bundle = "<key>AssociatedBundleIdentifiers</key><string>com.dictate.app</string>" if frozen() else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        f'<plist version="1.0"><dict><key>Label</key><string>{label}</string>'
        f"<key>ProgramArguments</key><array>{items}</array>{bundle}"
        "<key>RunAtLoad</key><true/><key>ProcessType</key><string>Interactive</string>"
        f"<key>StandardOutPath</key><string>{escape(str(log))}</string>"
        f"<key>StandardErrorPath</key><string>{escape(str(log))}</string>"
        "</dict></plist>\n"
    )


# -- Windows: values under HKCU\...\Run ----------------------------------------------------------------
def _win_get(name: str) -> str | None:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            return str(winreg.QueryValueEx(key, name)[0])
    except OSError:
        return None


def _win_set(name: str, command: str) -> None:
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, command)


def _win_delete(name: str) -> None:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, name)
    except OSError:
        pass


def _win_old_source_entry() -> str | None:
    """Older source checkouts registered themselves under the app's name ("Dictate")."""
    command = _win_get(_RUN_VALUE)
    return command if command and _SOURCE_MARK in command else None


def _win_is_enabled() -> bool:
    if frozen():
        return _win_get(_RUN_VALUE) is not None and _win_old_source_entry() is None
    return (
        _win_get(_SOURCE_RUN_VALUE) is not None
        or _win_old_source_entry() is not None
        or _legacy_windows_script().exists()
    )


def _win_forget_old_source_entries() -> None:
    if _win_old_source_entry():
        _win_delete(_RUN_VALUE)
    _legacy_windows_script().unlink(missing_ok=True)


# -- macOS: LaunchAgents ------------------------------------------------------------------------------
def _mac_old_source_entry() -> str | None:
    """Older source checkouts used the app's label (com.dictate.app)."""
    path = _mac_plist(LABEL)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return text if _SOURCE_MARK in text else None


# -- public ---------------------------------------------------------------------------------------------
def is_enabled() -> bool:
    if sys.platform == "win32":
        return _win_is_enabled()
    if sys.platform == "darwin":
        if frozen():
            return _mac_plist().exists() and _mac_old_source_entry() is None
        return _mac_plist().exists() or _mac_old_source_entry() is not None
    return False


def enable(mode: str = "client") -> str:
    args = launch_command(mode)
    if sys.platform == "win32":
        if frozen():
            if old := _win_old_source_entry():  # move a source checkout's entry aside, don't overwrite it
                _win_set(_SOURCE_RUN_VALUE, old)
            _win_set(_RUN_VALUE, _windows_command_line(args))
        else:
            _win_forget_old_source_entries()
            _win_set(_SOURCE_RUN_VALUE, _windows_command_line(args))
        return "dictate will start when you log in (Task Manager > Startup apps lists it)."
    if sys.platform == "darwin":
        old = _mac_old_source_entry()
        if old:  # move a source checkout's entry aside, don't overwrite it
            old_label = f"<key>Label</key><string>{LABEL}</string>"
            _mac_plist(SOURCE_LABEL).write_text(
                old.replace(old_label, f"<key>Label</key><string>{SOURCE_LABEL}</string>"), encoding="utf-8"
            )
            _mac_plist(LABEL).unlink(missing_ok=True)
        path = _mac_plist()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(mac_plist_text(args), encoding="utf-8")
        # clear a "disabled" mark left by an older `launchctl unload -w` (this doesn't start it now)
        label = LABEL if frozen() else SOURCE_LABEL
        subprocess.run(["launchctl", "enable", f"gui/{os.getuid()}/{label}"], capture_output=True, check=False)
        return "dictate will start when you log in (System Settings > General > Login Items lists it)."
    raise SystemExit("autostart supports Windows and macOS")


def disable() -> str:
    was = is_enabled()
    if sys.platform == "win32":
        if frozen():
            if _win_old_source_entry() is None:
                _win_delete(_RUN_VALUE)
        else:
            _win_delete(_SOURCE_RUN_VALUE)
            _win_forget_old_source_entries()
    elif sys.platform == "darwin":  # not unloaded: that would quit the running copy
        if frozen():
            if _mac_old_source_entry() is None:
                _mac_plist().unlink(missing_ok=True)
        else:
            _mac_plist().unlink(missing_ok=True)
            if _mac_old_source_entry():
                _mac_plist(LABEL).unlink(missing_ok=True)
    else:
        raise SystemExit("autostart supports Windows and macOS")
    return "dictate won't start at login anymore." if was else "autostart was not on."
