"""Small native dialogs for the desktop app, which has no terminal: AppleScript on macOS (always
there, looks native), tkinter on Windows. Every call blocks until the person answers, so call them
from a worker thread, never from the tray's own thread."""

from __future__ import annotations

import logging
import queue
import subprocess
import sys
import threading
from collections.abc import Callable

log = logging.getLogger(__name__)


def message(title: str, text: str, buttons: tuple[str, ...] = ("OK",), default: str = "") -> str | None:
    """Show `text` with `buttons` (the last one is the main one). Returns the button pressed, or None
    if the window was closed."""
    return _dialog(title, text, buttons, default or buttons[-1], entry=None)


def ask(title: str, text: str, default: str = "", ok: str = "OK", *, secret: bool = False) -> str | None:
    """Ask for one line of text (`secret`: shown as dots, for API keys). None if cancelled."""
    result = _dialog(title, text, ("Cancel", ok), ok, entry=default, secret=secret)
    return result if result is not None and not result.startswith("\0") else None


def choose(title: str, text: str, options: list[tuple[str, str]], ok: str = "Continue") -> str | None:
    """Pick one of `options` ((key, label) pairs). Returns the key, or None if cancelled."""
    try:
        if sys.platform == "darwin":
            return _mac_choose(title, text, options, ok)
        return _win_choose(title, text, options, ok)
    except Exception:
        log.exception("could not show a choice: %s", text)
        return None


def progress[T](title: str, task: Callable[[Callable[[str, float | None], None]], T]) -> T:
    """Run `task(report)` in a worker thread while a window shows `report(text, fraction)` (fraction None:
    unknown). Returns what the task returns; re-raises what it raised. Only Windows shows a window."""
    if sys.platform != "win32":
        return task(lambda text, fraction: log.info("%s", text.splitlines()[0] if text else ""))
    return _win_progress(title, task)


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
        if theAnswer starts with "*secret*" then
            set r to display dialog theText with title theTitle buttons theButtons default button theDefault default answer "" with icon note with hidden answer
        else
            set r to display dialog theText with title theTitle buttons theButtons default button theDefault default answer theAnswer with icon note
        end if
        return (button returned of r) & linefeed & (text returned of r)
    end if
end run"""


def _mac(title: str, text: str, buttons: tuple[str, ...], entry: str | None, secret: bool = False) -> str | None:
    args = [title, text, "-" if entry is None else ("*secret*" if secret else entry), *buttons]
    r = subprocess.run(["osascript", "-e", _OSA, *args], capture_output=True, text=True)
    if r.returncode != 0:  # Cancel or closed
        return None
    out = r.stdout.rstrip("\n")
    if entry is None:
        return out
    button, _, typed = out.partition("\n")
    if secret:
        typed = typed.strip()
    return typed if button == buttons[-1] else "\0" + button


_OSA_CHOOSE = """on run argv
    activate
    set theTitle to item 1 of argv
    set theText to item 2 of argv
    set okButton to item 3 of argv
    set theItems to items 4 thru -1 of argv
    set picked to choose from list theItems with title theTitle with prompt theText OK button name okButton default items {item 1 of theItems}
    if picked is false then return ""
    return item 1 of picked
end run"""


def _mac_choose(title: str, text: str, options: list[tuple[str, str]], ok: str) -> str | None:
    labels = [label for _, label in options]
    r = subprocess.run(["osascript", "-e", _OSA_CHOOSE, title, text, ok, *labels], capture_output=True, text=True)
    picked = r.stdout.strip()
    return next((key for key, label in options if label == picked), None)


# -- Windows ----------------------------------------------------------------------------------------
def _win(
    title: str, text: str, buttons: tuple[str, ...], default: str, entry: str | None, secret: bool = False
) -> str | None:
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
        field = ttk.Entry(frame, textvariable=var, width=64, show="•" if secret else "")
        field.pack(fill="x", pady=(12, 0))
        field.focus_set()
        field.select_range(0, "end")
    row = ttk.Frame(frame)
    row.pack(anchor="e", pady=(16, 0))

    def press(label: str) -> None:
        if entry is not None:
            result[0] = var.get().strip() if label == buttons[-1] else "\0" + label
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


def _win_window(title: str):
    import tkinter as tk

    root = tk.Tk()
    root.title(title)
    root.resizable(False, False)
    root.attributes("-topmost", True)
    return root


def _win_place(root) -> None:
    root.update_idletasks()
    w, h = root.winfo_reqwidth(), root.winfo_reqheight()
    root.geometry(f"+{(root.winfo_screenwidth() - w) // 2}+{(root.winfo_screenheight() - h) // 3}")
    root.lift()
    root.focus_force()


def _win_choose(title: str, text: str, options: list[tuple[str, str]], ok: str) -> str | None:
    import tkinter as tk
    from tkinter import ttk

    result: list[str | None] = [None]
    root = _win_window(title)
    frame = ttk.Frame(root, padding=18)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text=text, wraplength=480, justify="left").pack(anchor="w")
    var = tk.StringVar(value=options[0][0])
    for key, label in options:
        ttk.Radiobutton(frame, text=label, value=key, variable=var).pack(anchor="w", pady=(10, 0))
    row = ttk.Frame(frame)
    row.pack(anchor="e", pady=(18, 0))

    def done(pick: bool) -> None:
        result[0] = var.get() if pick else None
        root.destroy()

    ttk.Button(row, text="Cancel", command=lambda: done(False)).pack(side="left", padx=(8, 0))
    b = ttk.Button(row, text=ok, command=lambda: done(True), default="active")
    b.pack(side="left", padx=(8, 0))
    b.focus_set()
    root.bind("<Return>", lambda _e: done(True))
    root.bind("<Escape>", lambda _e: done(False))
    _win_place(root)
    root.mainloop()
    return result[0]


def _win_progress[T](title: str, task: Callable[[Callable[[str, float | None], None]], T]) -> T:
    from tkinter import ttk

    updates: queue.Queue[tuple[str, float | None]] = queue.Queue()
    outcome: list = []

    def work() -> None:
        try:
            outcome.append(("ok", task(lambda text, fraction: updates.put((text, fraction)))))
        except BaseException as e:
            outcome.append(("error", e))

    root = _win_window(title)
    root.protocol("WM_DELETE_WINDOW", lambda: None)  # can't be closed half-way
    frame = ttk.Frame(root, padding=18)
    frame.pack(fill="both", expand=True)
    label = ttk.Label(frame, text="Starting…", wraplength=480, justify="left", width=70)
    label.pack(anchor="w")
    bar = ttk.Progressbar(frame, length=480, mode="determinate", maximum=1.0)
    bar.pack(fill="x", pady=(14, 0))
    _win_place(root)
    threading.Thread(target=work, name="progress-task", daemon=True).start()

    def poll() -> None:
        while not updates.empty():
            text, fraction = updates.get_nowait()
            label.configure(text=text)
            if fraction is not None:
                bar.configure(value=fraction)
        if outcome:
            root.destroy()
            return
        root.after(150, poll)

    root.after(150, poll)
    root.mainloop()
    kind, value = outcome[0]
    if kind == "error":
        raise value
    return value


def _dialog(
    title: str, text: str, buttons: tuple[str, ...], default: str, entry: str | None, secret: bool = False
) -> str | None:
    try:
        if sys.platform == "darwin":
            return _mac(title, text, buttons, entry, secret)
        return _win(title, text, buttons, default, entry, secret)
    except Exception:
        log.exception("could not show a dialog: %s", text)
        return None
