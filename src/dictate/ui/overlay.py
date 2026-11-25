"""Live preview overlay: a small always-on-top pill that never takes keyboard focus.

Focus safety (the paste must land in the app you're typing in):
  Qt flags   Tool | Frameless | StaysOnTop | WindowDoesNotAcceptFocus | WindowTransparentForInput
  Qt attrs   WA_ShowWithoutActivating (show() uses SW_SHOWNOACTIVATE), WA_TransparentForMouseEvents
  Win32      WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TRANSPARENT | WS_EX_TOPMOST
Click-through: mouse events pass to the window underneath.

Other threads call `OverlayBridge` methods; they emit Qt signals, which Qt delivers on the UI
thread (queued connections are thread-safe).
"""

from __future__ import annotations

import ctypes
import html
import sys

from PySide6.QtCore import QEasingCurve, QObject, QPropertyAnimation, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QGuiApplication, QPainter, QPainterPath
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from dictate.config import PreviewConfig

MAX_CHARS = 280
BG = QColor(24, 24, 27, 236)
TEXT = "#f4f4f5"
DIM = "#8b8b94"
ACCENT = "#f87171"


class PreviewOverlay(QWidget):
    def __init__(self, cfg: PreviewConfig) -> None:
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.NoDropShadowWindowHint,
        )
        self.cfg = cfg
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedWidth(cfg.width)

        self.status = QLabel(self)
        self.status.setTextFormat(Qt.TextFormat.RichText)
        self.status.setFont(QFont("Segoe UI", max(8, cfg.font_size - 4)))
        self.text = QLabel(self)
        self.text.setTextFormat(Qt.TextFormat.RichText)
        self.text.setWordWrap(True)
        self.text.setFont(QFont("Segoe UI", cfg.font_size))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 10, 18, 12)
        layout.setSpacing(2)
        layout.addWidget(self.status)
        layout.addWidget(self.text)

        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(260)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade.finished.connect(self._after_fade)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.fade_out)
        self._apply_win32_styles()

    # -- Win32: belt and braces against focus stealing -------------------------------------------
    def _apply_win32_styles(self) -> None:
        if sys.platform != "win32":
            return
        user32 = ctypes.windll.user32
        user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        user32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
        user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
        hwnd = int(self.winId())
        gwl_exstyle = -20
        ex = user32.GetWindowLongPtrW(hwnd, gwl_exstyle)
        ex |= 0x08000000 | 0x00000080 | 0x00000020 | 0x00000008  # NOACTIVATE TOOLWINDOW TRANSPARENT TOPMOST
        user32.SetWindowLongPtrW(hwnd, gwl_exstyle, ex)

    def showEvent(self, event) -> None:
        self._apply_win32_styles()
        super().showEvent(event)

    # -- painting / placement ----------------------------------------------------------------------
    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 14, 14)
        p.fillPath(path, BG)

    def _place(self) -> None:
        self.adjustSize()
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        x = geo.x() + (geo.width() - self.width()) // 2
        y = geo.y() + 28 if self.cfg.position == "top" else geo.y() + geo.height() - self.height() - 28
        self.move(x, y)

    def _show(self) -> None:
        self._fade.stop()
        self._hide_timer.stop()
        self.setWindowOpacity(1.0)
        self._place()
        if not self.isVisible():
            self.show()  # WA_ShowWithoutActivating -> SW_SHOWNOACTIVATE

    # -- states --------------------------------------------------------------------------------
    def show_listening(self, hands_free: bool = False) -> None:
        label = "Hands-free" if hands_free else "Listening"
        self.status.setText(f'<span style="color:{ACCENT}">●</span> <span style="color:{DIM}">{label}</span>')
        self.text.setText(f'<span style="color:{DIM}">…</span>')
        self._show()

    def set_transcript(self, committed: str, stable: str, unstable: str) -> None:
        if not self.isVisible():
            return
        body = " ".join(x for x in (committed, stable) if x)
        tail = unstable
        full = (body + " " + tail).strip()
        if len(full) > MAX_CHARS:  # keep the end visible
            cut = len(full) - MAX_CHARS
            if cut < len(body):
                body = "…" + body[cut:]
            else:
                body, tail = "…", tail[cut - len(body) :]
        rich = f'<span style="color:{TEXT}">{html.escape(body)}</span>'
        if tail:
            rich += f' <span style="color:{DIM}">{html.escape(tail)}</span>'
        self.text.setText(rich or f'<span style="color:{DIM}">…</span>')
        self._place()

    def show_status(self, label: str) -> None:
        if not self.isVisible():
            return
        self.status.setText(f'<span style="color:{DIM}">{html.escape(label)}</span>')

    def show_result(self, text: str) -> None:
        if not self.isVisible():
            return
        shown = text if len(text) <= MAX_CHARS else "…" + text[-MAX_CHARS:]
        self.status.setText(f'<span style="color:{DIM}">Inserted</span>')
        self.text.setText(f'<span style="color:{TEXT}">{html.escape(shown).replace(chr(10), "<br>")}</span>')
        self._place()
        self._hide_timer.start(max(0, self.cfg.show_result_ms))

    def fade_out(self) -> None:
        if not self.isVisible():
            return
        self._fade.stop()
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(0.0)
        self._fade.start()

    def _after_fade(self) -> None:
        if self.windowOpacity() <= 0.01:
            self.hide()
            self.setWindowOpacity(1.0)


class OverlayBridge(QObject):
    """Thread-safe facade: call these from any thread."""

    _listening = Signal(bool)
    _transcript = Signal(str, str, str)
    _status = Signal(str)
    _result = Signal(str)
    _hide = Signal()

    def __init__(self, overlay: PreviewOverlay) -> None:
        super().__init__()
        self._listening.connect(overlay.show_listening)
        self._transcript.connect(overlay.set_transcript)
        self._status.connect(overlay.show_status)
        self._result.connect(overlay.show_result)
        self._hide.connect(overlay.fade_out)

    def listening(self, hands_free: bool = False) -> None:
        self._listening.emit(hands_free)

    def transcript(self, committed: str, stable: list[str], unstable: list[str]) -> None:
        self._transcript.emit(committed, " ".join(stable), " ".join(unstable))

    def status(self, label: str) -> None:
        self._status.emit(label)

    def result(self, text: str) -> None:
        self._result.emit(text)

    def hide(self) -> None:
        self._hide.emit()
