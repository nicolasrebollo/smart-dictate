"""Small native dialogs for the desktop app, which has no terminal: AppleScript on macOS (always
there, looks native), tkinter on Windows. Every call blocks until the person answers, so call them
from a worker thread, never from the tray's own thread."""

from __future__ import annotations

import logging
import subprocess
import sys

log = logging.getLogger(__name__)


def message(title: str, text: str, buttons: tuple[str, ...] = ("OK",), default: str = "") -> str | None:
    """Show `text` with `buttons` (the last one is the main one). Returns the button pressed, or None
    if the window was closed."""
    return _dialog(title, text, buttons, default or buttons[-1], entry=None)


def ask(title: str, text: str, default: str = "", ok: str = "OK") -> str | None:
    """Ask for one line of text. None if cancelled."""
    result = _dialog(title, text, ("Cancel", ok), ok, entry=default)
    return result if result is not None and not result.startswith("\0") else None


def clipboard_text() -> str:
    try:
        if sys.platform == "darwin":
            return subprocess.run(["pbpaste"], capture_output=True, timeout=2).stdout.decode("utf-8", "replace")
        import tkinter

        root = tkinter.Tk()
        root.withdraw()
        try:
            return root.clipboard_get()
        finally:
            root.destroy()
    except Exception:
        return ""


# -- macOS ------------------------------------------------------------------------------------------
_OSA = """on run argv
    activate
    set theButtons to items 4 thru -1 of argv
    set theTitle to item 1 of argv
    set theText to item 2 of argv
    set theDefault to last item of theButtons
    if item 3 of argv is "-" then
        set r to display dialog theText with title theTitle buttons theButtons default button theDefault with icon note
        return button returned of r
    else
        set theAnswer to item 3 of argv
        set r to display dialog theText with title theTitle buttons theButtons default button theDefault default answer theAnswer with icon note
        return (button returned of r) & linefeed & (text returned of r)
    end if
end run"""


def _mac(title: str, text: str, buttons: tuple[str, ...], entry: str | None) -> str | None:
    args = [title, text, "-" if entry is None else entry, *buttons]
    r = subprocess.run(["osascript", "-e", _OSA, *args], capture_output=True, text=True)
    if r.returncode != 0:  # Cancel or closed
        return None
    out = r.stdout.rstrip("\n")
    if entry is None:
        return out
    button, _, typed = out.partition("\n")
    return typed if button == buttons[-1] else "\0" + button


# -- Windows ----------------------------------------------------------------------------------------
def _win(title: str, text: str, buttons: tuple[str, ...], default: str, entry: str | None) -> str | None:
    import tkinter as tk
    from tkinter import ttk

    result: list[str | None] = [None]
    root = tk.Tk()
    root.title(title)
    root.resizable(False, False)
    root.attributes("-topmost", True)
    frame = ttk.Frame(root, padding=18)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text=text, wraplength=440, justify="left").pack(anchor="w")
    var = tk.StringVar(value=entry or "")
    if entry is not None:
        field = ttk.Entry(frame, textvariable=var, width=64)
        field.pack(fill="x", pady=(12, 0))
        field.focus_set()
        field.select_range(0, "end")
    row = ttk.Frame(frame)
    row.pack(anchor="e", pady=(16, 0))

    def press(label: str) -> None:
        if entry is not None:
            result[0] = var.get() if label == buttons[-1] else "\0" + label
        else:
            result[0] = label
        root.destroy()

    for label in buttons:
        b = ttk.Button(row, text=label, command=lambda lab=label: press(lab))
        b.pack(side="left", padx=(8, 0))
        if label == default:
            b.configure(default="active")
            if entry is None:
                b.focus_set()
    root.bind("<Return>", lambda _e: press(default))
    root.bind("<Escape>", lambda _e: root.destroy())
    root.update_idletasks()
    w, h = root.winfo_reqwidth(), root.winfo_reqheight()
    root.geometry(f"+{(root.winfo_screenwidth() - w) // 2}+{(root.winfo_screenheight() - h) // 3}")
    root.lift()
    root.focus_force()
    root.mainloop()
    return result[0]


def _dialog(title: str, text: str, buttons: tuple[str, ...], default: str, entry: str | None) -> str | None:
    try:
        if sys.platform == "darwin":
            return _mac(title, text, buttons, entry)
        return _win(title, text, buttons, default, entry)
    except Exception:
        log.exception("could not show a dialog: %s", text)
        return None
