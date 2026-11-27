"""macOS: insert text at the cursor by pasting (Cmd+V), then put the previous clipboard back.

Uses the system `pbcopy`/`pbpaste` tools, so only plain text on the clipboard is restored (an
image you had copied is replaced). Cmd+V is posted with Quartz (no pynput: its macOS keyboard code
can crash Python on recent macOS). Needs Accessibility permission.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from dataclasses import dataclass

_ENV = {**os.environ, "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8"}


@dataclass
class InsertOutcome:
    method: str
    ok: bool
    note: str = ""


def _pbpaste() -> str:
    return subprocess.run(["pbpaste"], capture_output=True, env=_ENV, timeout=2).stdout.decode("utf-8", "replace")


def _pbcopy(text: str) -> None:
    subprocess.run(["pbcopy"], input=text.encode("utf-8"), env=_ENV, timeout=2, check=False)


class MacInserter:
    def __init__(self, *, restore_clipboard: bool = True, restore_delay_ms: int = 400) -> None:
        self.restore_clipboard = restore_clipboard
        self.restore_delay_ms = restore_delay_ms
        self.injecting = False  # the hotkey listener ignores keys while we press Cmd+V
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._saved: str | None = None  # the user's clipboard while ours is on it
        self._ours: str | None = None

    def prefetch(self) -> None:
        pass

    def insert(self, text: str, *, live: bool = False) -> InsertOutcome:
        """live is accepted for symmetry: ⌘V is sent with only Command set, whatever else is held."""
        if not text:
            return InsertOutcome("none", True)
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()  # keep the original clipboard from the first paste
                self._timer = None
            if self.restore_clipboard and self._saved is None:
                self._saved = _pbpaste()
            _pbcopy(text)
            self._ours = text
        from dictate.hotkeys_mac import press_cmd_v

        self.injecting = True
        try:
            press_cmd_v()
        finally:
            time.sleep(0.05)
            self.injecting = False
        if self.restore_clipboard:
            with self._lock:
                self._timer = threading.Timer(self.restore_delay_ms / 1000, self._restore)
                self._timer.daemon = True
                self._timer.start()
        return InsertOutcome("paste", True)

    def _restore(self) -> None:
        with self._lock:
            saved, ours = self._saved, self._ours
            self._saved, self._ours, self._timer = None, None, None
            if saved is not None and _pbpaste() == ours:  # don't clobber something copied since
                _pbcopy(saved)

    def flush(self) -> None:
        with self._lock:
            timer = self._timer
        if timer is not None:
            timer.cancel()
            self._restore()
