"""Locks that stop two copies of something running at once on this computer.

"app"    one copy of the installed app.
"hotkey" one hotkey client of any kind (the installed app, `dictate pc` or `dictate client`). Two of
         them would both hear the hotkey and type every dictation twice.

A lock is held while its handle stays alive and is released when the process exits (even if it
crashes), so a stale lock can't block the next start.
"""

from __future__ import annotations

import sys
from typing import Any

from dictate import paths

_WINDOWS_NAMES = {"app": "Local\\DictateDesktopApp", "hotkey": "Local\\DictateHotkey"}


def hold(name: str) -> Any:
    """A handle that keeps `name` held while it's alive, or None if another process holds it."""
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        handle = kernel32.CreateMutexW(None, False, _WINDOWS_NAMES.get(name, f"Local\\Dictate-{name}"))
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            kernel32.CloseHandle(ctypes.c_void_p(handle))
            return None
        return handle
    import fcntl

    path = paths.data_dir() / f"{name}.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    f = open(path, "w")  # noqa: SIM115 - held open for the life of the process
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f
