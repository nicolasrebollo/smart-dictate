"""Insert text at the cursor of the focused app.

Default: clipboard paste. We snapshot the user's clipboard (all copyable formats), put our
text on it (flagged so Windows clipboard history / cloud sync skip it), send Ctrl+V, then
restore the original after a short delay - but only if nothing else changed the clipboard
in the meantime. Fallback: simulated Unicode typing via SendInput.
"""

from __future__ import annotations

import ctypes
import logging
import threading
import time
from dataclasses import dataclass

from dictate import platform_win as w

log = logging.getLogger(__name__)

MAX_SNAPSHOT_BYTES = 64 * 1024 * 1024


class ClipboardBusyError(RuntimeError):
    pass


@dataclass
class InsertOutcome:
    method: str
    ok: bool
    note: str = ""


class _Clipboard:
    def __init__(self) -> None:
        self._hwnd = w.create_pumped_message_window()
        self._fmt_exclude = w.user32.RegisterClipboardFormatW("ExcludeClipboardContentFromMonitorProcessing")
        self._fmt_history = w.user32.RegisterClipboardFormatW("CanIncludeInClipboardHistory")
        self._fmt_cloud = w.user32.RegisterClipboardFormatW("CanUploadToCloudClipboard")

    def _open(self) -> None:
        for _ in range(50):  # another app may hold the clipboard for a few ms
            if w.user32.OpenClipboard(self._hwnd):
                return
            time.sleep(0.005)
        raise ClipboardBusyError("clipboard is busy")

    def sequence(self) -> int:
        return int(w.user32.GetClipboardSequenceNumber())

    def snapshot(self) -> list[tuple[int, bytes]] | None:
        """All HGLOBAL formats currently on the clipboard. None if too large to hold."""
        items: list[tuple[int, bytes]] = []
        total = 0
        self._open()
        try:
            fmt = 0
            while fmt := w.user32.EnumClipboardFormats(fmt):
                if not w.is_safe_format(fmt):
                    continue
                handle = w.user32.GetClipboardData(fmt)
                if not handle:
                    continue
                size = w.kernel32.GlobalSize(handle)
                if not size:
                    continue
                total += size
                if total > MAX_SNAPSHOT_BYTES:
                    return None
                ptr = w.kernel32.GlobalLock(handle)
                if not ptr:
                    continue
                try:
                    items.append((fmt, ctypes.string_at(ptr, size)))
                finally:
                    w.kernel32.GlobalUnlock(handle)
        finally:
            w.user32.CloseClipboard()
        return items

    def _set_handle(self, fmt: int, data: bytes) -> None:
        h = w.kernel32.GlobalAlloc(w.GMEM_MOVEABLE, max(1, len(data)))
        if not h:
            raise MemoryError("GlobalAlloc failed")
        ptr = w.kernel32.GlobalLock(h)
        ctypes.memmove(ptr, data, len(data))
        w.kernel32.GlobalUnlock(h)
        if not w.user32.SetClipboardData(fmt, h):
            w.kernel32.GlobalFree(h)  # ownership only transfers on success

    def set_text(self, text: str) -> int:
        self._open()
        try:
            w.user32.EmptyClipboard()
            self._set_handle(w.CF_UNICODETEXT, (text + "\0").encode("utf-16-le"))
            zero = (0).to_bytes(4, "little")
            self._set_handle(self._fmt_exclude, zero)
            self._set_handle(self._fmt_history, zero)
            self._set_handle(self._fmt_cloud, zero)
        finally:
            w.user32.CloseClipboard()
        return self.sequence()

    def restore(self, items: list[tuple[int, bytes]]) -> None:
        self._open()
        try:
            w.user32.EmptyClipboard()
            for fmt, data in items:
                try:
                    self._set_handle(fmt, data)
                except Exception:  # one odd format must not lose the rest
                    log.debug("could not restore clipboard format %s", fmt)
        finally:
            w.user32.CloseClipboard()


class TextInserter:
    def __init__(
        self,
        *,
        method: str = "paste",
        restore_clipboard: bool = True,
        restore_delay_ms: int = 400,
        type_apps: list[str] | None = None,
        newline_key: str = "shift+enter",
    ) -> None:
        self.method = method
        self.restore_clipboard = restore_clipboard
        self.restore_delay_ms = restore_delay_ms
        self.type_apps = {a.lower() for a in (type_apps or [])}
        self.newline_key = newline_key
        self._clip = _Clipboard()
        self._lock = threading.Lock()
        self._pending_restore: threading.Timer | None = None
        self._saved: list[tuple[int, bytes]] | None = None  # the user's clipboard, while ours is on it
        self._saved_seq: int | None = None
        self._prefetched: tuple[int, list[tuple[int, bytes]] | None] | None = None
        self._self_elevated = w.self_is_elevated()

    def update(
        self,
        *,
        method: str,
        restore_clipboard: bool,
        restore_delay_ms: int,
        type_apps: list[str],
        newline_key: str,
    ) -> None:
        self.method, self.restore_clipboard, self.restore_delay_ms = (
            method,
            restore_clipboard,
            restore_delay_ms,
        )
        self.type_apps = {a.lower() for a in type_apps}
        self.newline_key = newline_key

    def prefetch(self) -> None:
        """Snapshot the clipboard while the user is still speaking (saves time at release)."""
        if self.method != "paste" or not self.restore_clipboard:
            return
        with self._lock:
            if self._saved is not None:
                return  # a restore is still pending: the real user clipboard is in self._saved
            try:
                seq = self._clip.sequence()
                self._prefetched = (seq, self._clip.snapshot())
            except ClipboardBusyError:
                self._prefetched = None

    def insert(self, text: str) -> InsertOutcome:
        if not text:
            return InsertOutcome("none", True)
        fg = w.foreground_info()
        if fg.elevated is None and not self._self_elevated and fg.exe:
            log.warning(
                "focused app %s may be running as administrator; Windows blocks synthetic input "
                "into it. If nothing appears, run dictate as administrator or press Ctrl+V.",
                fg.exe,
            )
        method = "type" if (self.method == "type" or fg.exe in self.type_apps) else "paste"
        if method == "paste":
            try:
                return self._paste(text, blocked_hint=fg.elevated is None and not self._self_elevated)
            except ClipboardBusyError:
                log.warning("clipboard busy; typing instead")
        return self._type(text)

    # -- paste --------------------------------------------------------------------------
    def _paste(self, text: str, *, blocked_hint: bool) -> InsertOutcome:
        with self._lock:
            if self._pending_restore is not None:
                self._pending_restore.cancel()  # keep the original snapshot from the first paste
                self._pending_restore = None
            if self.restore_clipboard and self._saved is None:
                seq_now = self._clip.sequence()
                if self._prefetched is not None and self._prefetched[0] == seq_now:
                    snap = self._prefetched[1]
                else:
                    snap = self._clip.snapshot()
                self._prefetched = None
                if snap is None:
                    log.info("clipboard holds >64 MB; typing instead of pasting so it isn't lost")
                    return self._type(text)
                self._saved = snap
            our_seq = self._clip.set_text(text)
            self._saved_seq = our_seq

        _wait_modifiers_released()
        w.send_inputs(
            [
                w.key_input(w.VK_CONTROL),
                w.key_input(w.VK_V),
                w.key_input(w.VK_V, flags=w.KEYEVENTF_KEYUP),
                w.key_input(w.VK_CONTROL, flags=w.KEYEVENTF_KEYUP),
            ]
        )

        if self.restore_clipboard and not blocked_hint:
            with self._lock:
                timer = threading.Timer(self.restore_delay_ms / 1000, self._restore)
                timer.daemon = True
                self._pending_restore = timer
                timer.start()
        elif blocked_hint:
            # Leave the text on the clipboard so the user can paste it by hand.
            with self._lock:
                self._saved, self._saved_seq = None, None
            return InsertOutcome("paste", False, "target may be elevated; text left on clipboard")
        return InsertOutcome("paste", True)

    def _restore(self) -> None:
        with self._lock:
            self._pending_restore = None
            saved, seq = self._saved, self._saved_seq
            self._saved, self._saved_seq = None, None
            if saved is None:
                return
            if self._clip.sequence() != seq:
                log.debug("clipboard changed since paste; not restoring")
                return
            try:
                self._clip.restore(saved)
            except ClipboardBusyError:
                log.warning("could not restore clipboard (busy)")

    def flush(self) -> None:
        """Restore immediately (used at shutdown)."""
        with self._lock:
            timer = self._pending_restore
        if timer is not None:
            timer.cancel()
            self._restore()

    # -- type ---------------------------------------------------------------------------
    def _type(self, text: str) -> InsertOutcome:
        _wait_modifiers_released()
        inputs: list[w.INPUT] = []
        for ch in text.replace("\r\n", "\n"):
            if ch == "\n":
                if self.newline_key == "shift+enter":
                    inputs.append(w.key_input(w.VK_SHIFT))
                inputs += [w.key_input(w.VK_RETURN), w.key_input(w.VK_RETURN, flags=w.KEYEVENTF_KEYUP)]
                if self.newline_key == "shift+enter":
                    inputs.append(w.key_input(w.VK_SHIFT, flags=w.KEYEVENTF_KEYUP))
                continue
            for unit in _utf16_units(ch):
                inputs.append(w.key_input(scan=unit, flags=w.KEYEVENTF_UNICODE))
                inputs.append(w.key_input(scan=unit, flags=w.KEYEVENTF_UNICODE | w.KEYEVENTF_KEYUP))
        sent = w.send_inputs(inputs)
        ok = sent == len(inputs)
        return InsertOutcome("type", ok, "" if ok else f"SendInput sent {sent}/{len(inputs)} events")


def _utf16_units(ch: str) -> list[int]:
    data = ch.encode("utf-16-le")
    return [int.from_bytes(data[i : i + 2], "little") for i in range(0, len(data), 2)]


def _wait_modifiers_released(timeout_s: float = 0.5) -> None:
    """Ctrl+V while the user still holds Shift becomes Ctrl+Shift+V (a different shortcut in
    many apps), so wait briefly for physical modifiers to be released."""
    deadline = time.perf_counter() + timeout_s
    mods = (w.VK_SHIFT, w.VK_MENU, w.VK_LWIN, w.VK_RWIN, w.VK_CONTROL)
    while any(w.key_is_down(vk) for vk in mods):
        if time.perf_counter() > deadline:
            log.debug("modifier still held after %.0f ms; inserting anyway", timeout_s * 1000)
            return
        time.sleep(0.005)
