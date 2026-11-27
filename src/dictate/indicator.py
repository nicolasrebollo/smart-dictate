"""The on-screen sign that Dictate is listening: a small badge at the top of the screen, "● Recording"
(red, pulsing) while it listens and "● Transcribing…" (amber) until the text arrives. It never takes the
focus and clicks go through it, so it can't get in the way of the app you're typing into.

  Windows  a layered popup window made with Windows' own API (a Tk window would take the focus when
           it's created), drawn with Pillow, on a thread of its own
  Mac      an NSPanel, made and changed on the main thread (the menu-bar icon's run loop)

It also leaves the state in a small file (recording.json in Dictate's data folder) for the Notes window,
which runs as a separate process. While listening, the file is touched every second; an old file
(Dictate crashed mid-dictation) counts as not recording.
"""

from __future__ import annotations

import contextlib
import json
import logging
import queue
import sys
import threading
import time
from pathlib import Path
from typing import Any

from dictate import paths

log = logging.getLogger(__name__)

RED, AMBER, DIM_RED = "#ff453a", "#ffb340", "#8c2a24"
MAX_AGE_S = 3.0  # an unrefreshed "listening" file older than this is stale


def state_path() -> Path:
    return paths.data_dir() / "recording.json"


def read_state(path: Path | None = None, now: float | None = None) -> str | None:
    """ "listening", "working", or None: what Dictate is doing right now (for the Notes window)."""
    path = path or state_path()
    try:
        age = (now if now is not None else time.time()) - path.stat().st_mtime
        state = json.loads(path.read_text(encoding="utf-8")).get("state")
    except (OSError, ValueError, AttributeError):
        return None
    if age > MAX_AGE_S or state not in ("listening", "working"):
        return None
    return state


def label_for(state: str, computer_audio: bool = False) -> str:
    if state == "working":
        return "Transcribing…"
    return "Recording computer audio" if computer_audio else "Recording"


class Indicator:
    """Thread-safe: show(state) from anywhere. state: "listening", "working", anything else hides it."""

    def __init__(self, *, enabled: bool = True, path: Path | None = None) -> None:
        self.enabled = enabled
        self._path = path or state_path()
        self._state = ""
        self._touched = 0.0
        self._badge: Any = None
        self._lock = threading.Lock()
        with contextlib.suppress(OSError):
            self._path.unlink(missing_ok=True)  # left over from a crash

    def show(self, state: str, *, computer_audio: bool = False) -> None:
        state = state if state in ("listening", "working") else ""
        with self._lock:
            if state == self._state:
                return
            self._state = state
        self._write(state)
        if not self.enabled:
            return
        try:
            badge = self._get_badge()
            if badge is not None:
                badge.show(state, label_for(state, computer_audio) if state else "")
        except Exception:
            log.debug("recording badge failed", exc_info=True)

    def heartbeat(self) -> None:
        """While listening (audio is flowing): keep the Notes window's dot on."""
        if self._state == "listening" and time.monotonic() - self._touched > 1.0:
            self._write("listening")

    def close(self) -> None:
        self.show("")
        if self._badge is not None:
            with contextlib.suppress(Exception):
                self._badge.close()

    def _write(self, state: str) -> None:
        try:
            if state:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                self._path.write_text(json.dumps({"state": state}), encoding="utf-8")
                self._touched = time.monotonic()
            else:
                self._path.unlink(missing_ok=True)
        except OSError:
            log.debug("couldn't write the recording state", exc_info=True)

    def _get_badge(self) -> Any:
        if self._badge is None:
            if sys.platform == "win32":
                self._badge = WindowsBadge()
            elif sys.platform == "darwin":
                self._badge = MacBadge()
        return self._badge


# -- the picture (Windows) ------------------------------------------------------------------------------
def render_badge(text: str, dot: str, scale: float = 1.0) -> Any:
    """The badge as an RGBA image: a dark pill with a colored dot and the text (drawn 4x and scaled
    down, for smooth edges)."""
    from PIL import Image, ImageDraw, ImageFont

    ss = 4
    k = scale * ss
    font: Any = None
    for name in ("segoeuib.ttf", "seguisb.ttf", "arialbd.ttf"):
        try:
            font = ImageFont.truetype(name, round(13 * k))
            break
        except OSError:
            continue
    if font is None:
        font = ImageFont.load_default(round(13 * k))
    h, pad, d, gap = round(30 * k), round(12 * k), round(10 * k), round(8 * k)
    w = round(pad + d + gap + font.getlength(text) + pad + 2 * k)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((0, 0, w - 1, h - 1), radius=h // 2, fill=(28, 28, 31, 238))
    cy = h // 2
    draw.ellipse((pad, cy - d // 2, pad + d, cy + d // 2), fill=dot)
    draw.text((pad + d + gap, cy), text, fill=(245, 245, 247, 255), font=font, anchor="lm")
    return img.resize((max(1, round(w / ss)), max(1, round(h / ss))), Image.Resampling.LANCZOS)


# -- Windows -------------------------------------------------------------------------------------------
class WindowsBadge:
    """A layered, click-through, never-activated popup window. It lives on a thread of its own that
    pumps its messages; show() queues the new state and wakes that thread."""

    WM_APP, WM_TIMER = 0x8000, 0x0113

    def __init__(self) -> None:
        self._q: queue.Queue[tuple[str, str] | None] = queue.Queue()
        self.hwnd = 0
        self._thread_id = 0
        self._ready = threading.Event()
        threading.Thread(target=self._run, name="recording-badge", daemon=True).start()
        self._ready.wait(3)

    def show(self, state: str, text: str) -> None:
        self._q.put((state, text))
        self._wake()

    def close(self) -> None:
        self._q.put(None)
        self._wake()

    def _wake(self) -> None:
        if self._thread_id:
            import ctypes

            ctypes.windll.user32.PostThreadMessageW(self._thread_id, self.WM_APP, 0, 0)

    def _run(self) -> None:
        try:
            self._loop()
        except Exception:
            log.exception("recording badge stopped")
        finally:
            self._ready.set()

    def _loop(self) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        gdi32, kernel32 = ctypes.WinDLL("gdi32"), ctypes.WinDLL("kernel32")
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.CreateWindowExW.argtypes = [
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
        ]  # fmt: skip
        user32.GetDC.restype = wintypes.HDC
        user32.GetDC.argtypes = [wintypes.HWND]
        user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND] + [ctypes.c_int] * 4 + [wintypes.UINT]
        user32.DestroyWindow.argtypes = [wintypes.HWND]
        user32.UpdateLayeredWindow.argtypes = [
            wintypes.HWND, wintypes.HDC, ctypes.c_void_p, ctypes.c_void_p, wintypes.HDC, ctypes.c_void_p,
            wintypes.COLORREF, ctypes.c_void_p, wintypes.DWORD,
        ]  # fmt: skip
        gdi32.CreateCompatibleDC.restype = wintypes.HDC
        gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
        gdi32.CreateDIBSection.restype = wintypes.HBITMAP
        gdi32.CreateDIBSection.argtypes = [
            wintypes.HDC, ctypes.c_void_p, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE,
            wintypes.DWORD,
        ]  # fmt: skip
        gdi32.SelectObject.restype = wintypes.HGDIOBJ
        gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
        gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        gdi32.DeleteDC.argtypes = [wintypes.HDC]

        # layered | click-through | no taskbar button | always on top | never activated; a hidden popup
        ex = 0x80000 | 0x20 | 0x80 | 0x8 | 0x8000000
        hwnd = user32.CreateWindowExW(
            ex, "STATIC", "Dictate is recording", 0x80000000, 0, 0, 1, 1, None, None, None, None
        )
        if not hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        self.hwnd = hwnd
        self._thread_id = kernel32.GetCurrentThreadId()
        try:
            scale = user32.GetDpiForSystem() / 96
        except (AttributeError, OSError):
            scale = 1.0

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [
                ("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD),
            ]  # fmt: skip

        class BLENDFUNCTION(ctypes.Structure):
            _fields_ = [("op", ctypes.c_ubyte), ("flags", ctypes.c_ubyte), ("alpha", ctypes.c_ubyte),
                        ("format", ctypes.c_ubyte)]  # fmt: skip

        def paint(img: Any) -> None:
            from PIL import Image

            w, h = img.size
            r, g, b, a = img.convert("RGBa").split()  # premultiplied alpha, as Windows wants it
            data = Image.merge("RGBA", (b, g, r, a)).tobytes()  # and in BGRA order
            screen = user32.GetDC(None)
            mem = gdi32.CreateCompatibleDC(screen)
            bmi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
            bits = ctypes.c_void_p()
            bmp = gdi32.CreateDIBSection(mem, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
            ctypes.memmove(bits, data, len(data))
            old = gdi32.SelectObject(mem, bmp)
            x = (user32.GetSystemMetrics(0) - w) // 2
            pos, size, src = wintypes.POINT(x, round(14 * scale)), wintypes.SIZE(w, h), wintypes.POINT(0, 0)
            blend = BLENDFUNCTION(0, 0, 255, 1)  # AC_SRC_OVER with the image's own alpha
            user32.UpdateLayeredWindow(
                hwnd, screen, ctypes.byref(pos), ctypes.byref(size), mem, ctypes.byref(src), 0,
                ctypes.byref(blend), 2,
            )  # fmt: skip
            gdi32.SelectObject(mem, old)
            gdi32.DeleteObject(bmp)
            gdi32.DeleteDC(mem)
            user32.ReleaseDC(None, screen)

        now: dict[str, Any] = {"state": "", "text": "", "dim": False}
        images: dict[tuple[str, str], Any] = {}

        def draw() -> None:
            color = AMBER if now["state"] == "working" else (DIM_RED if now["dim"] else RED)
            key = (now["text"], color)
            if key not in images:
                images[key] = render_badge(now["text"], color, scale)
            paint(images[key])

        self._ready.set()
        user32.SetTimer(None, 0, 600, None)  # the red dot pulses
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == self.WM_TIMER and not msg.hWnd:
                if now["state"] == "listening":
                    now["dim"] = not now["dim"]
                    draw()
                continue
            if msg.message == self.WM_APP and not msg.hWnd:
                while True:
                    try:
                        item = self._q.get_nowait()
                    except queue.Empty:
                        break
                    if item is None:
                        user32.DestroyWindow(hwnd)
                        return
                    now["state"], now["text"], now["dim"] = item[0], item[1], False
                    if item[0]:
                        draw()
                        user32.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE
                        # above other always-on-top windows: HWND_TOPMOST, no move / size / activate
                        user32.SetWindowPos(hwnd, wintypes.HWND(-1), 0, 0, 0, 0, 0x1 | 0x2 | 0x10)
                    else:
                        user32.ShowWindow(hwnd, 0)
                continue
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))


# -- Mac -----------------------------------------------------------------------------------------------
class MacBadge:
    """Everything here runs on the main thread (AppKit requires it), handed over with callAfter."""

    def __init__(self) -> None:
        self._panel: Any = None
        self._dot: Any = None
        self._label: Any = None

    def show(self, state: str, text: str) -> None:
        from PyObjCTools import AppHelper

        AppHelper.callAfter(self._show, state, text)

    def close(self) -> None:
        self.show("", "")

    def _make(self) -> None:
        from AppKit import (
            NSBackingStoreBuffered,
            NSColor,
            NSFont,
            NSMakeRect,
            NSPanel,
            NSStatusWindowLevel,
            NSTextField,
        )

        w, h = 210, 30
        borderless, nonactivating = 0, 1 << 7
        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, w, h), borderless | nonactivating, NSBackingStoreBuffered, False
        )
        panel.setLevel_(NSStatusWindowLevel)
        panel.setOpaque_(False)
        panel.setBackgroundColor_(NSColor.clearColor())
        panel.setIgnoresMouseEvents_(True)
        panel.setHasShadow_(True)
        panel.setHidesOnDeactivate_(False)
        panel.setCollectionBehavior_((1 << 0) | (1 << 4) | (1 << 8))  # all Spaces, stationary, over full screen
        view = panel.contentView()
        view.setWantsLayer_(True)
        view.layer().setCornerRadius_(h / 2)
        view.layer().setBackgroundColor_(NSColor.colorWithCalibratedWhite_alpha_(0.11, 0.94).CGColor())
        dot = NSTextField.labelWithString_("●")
        dot.setFont_(NSFont.systemFontOfSize_(13))
        dot.setFrame_(NSMakeRect(12, 6, 16, 18))
        label = NSTextField.labelWithString_("Recording")
        label.setFont_(NSFont.boldSystemFontOfSize_(12))
        label.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(0.96, 1.0))
        label.setFrame_(NSMakeRect(32, 6, w - 40, 17))
        view.addSubview_(dot)
        view.addSubview_(label)
        self._panel, self._dot, self._label = panel, dot, label

    def _show(self, state: str, text: str) -> None:
        try:
            from AppKit import NSColor, NSMakePoint, NSScreen

            if not state:
                if self._panel is not None:
                    self._panel.orderOut_(None)
                return
            if self._panel is None:
                self._make()
            r, g, b = (1.0, 0.27, 0.23) if state == "listening" else (1.0, 0.70, 0.25)
            self._dot.setTextColor_(NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, 1.0))
            self._label.setStringValue_(text)
            width = 44 + self._label.attributedStringValue().size().width
            frame = self._panel.frame()
            frame.size.width = width
            self._panel.setFrame_display_(frame, True)
            vf = NSScreen.mainScreen().visibleFrame()
            x = vf.origin.x + (vf.size.width - width) / 2
            y = vf.origin.y + vf.size.height - frame.size.height - 8
            self._panel.setFrameOrigin_(NSMakePoint(x, y))
            self._panel.orderFrontRegardless()
        except Exception:
            log.debug("recording badge failed", exc_info=True)
