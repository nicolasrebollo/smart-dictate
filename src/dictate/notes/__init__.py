"""Notes: a small notes app inside Dictate, kept on this computer (store.py: where they live;
window.py: the window, which runs in a process of its own)."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from typing import Any


def open_window(spawn: Callable[..., Any] = subprocess.Popen) -> None:
    """Open the Notes window in its own process (from the tray menu). If it's already open, that one
    comes to the front instead."""
    if getattr(sys, "frozen", False):
        args = [sys.executable, "--notes"]
    else:
        args = [sys.executable, "-m", "dictate.cli", "notes"]
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    spawn(args, close_fds=True, creationflags=flags, stdin=subprocess.DEVNULL)
