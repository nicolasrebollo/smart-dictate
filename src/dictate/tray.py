"""Tray icon (Windows) / menu-bar icon (Mac): shows the state, switches language, quits.

States: ready (white mic), listening (red), working (amber), offline (grey, crossed out).
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable

from PIL import Image, ImageDraw

from dictate.config import LANGUAGES

log = logging.getLogger(__name__)

COLORS = {"ready": (240, 240, 240), "listening": (230, 57, 70), "working": (245, 166, 35), "offline": (130, 130, 130)}
LANGUAGE_NAMES = {"en": "English", "es": "Español", "fr": "Français"}


def icon_image(state: str, size: int = 64) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = COLORS.get(state, COLORS["ready"])
    s = size / 64
    d.rounded_rectangle((22 * s, 6 * s, 42 * s, 40 * s), radius=10 * s, fill=c)
    d.arc((13 * s, 20 * s, 51 * s, 50 * s), 0, 180, fill=c, width=max(2, round(5 * s)))
    d.line((32 * s, 50 * s, 32 * s, 58 * s), fill=c, width=max(2, round(5 * s)))
    d.line((22 * s, 58 * s, 42 * s, 58 * s), fill=c, width=max(2, round(5 * s)))
    if state == "offline":
        d.line((8 * s, 8 * s, 56 * s, 56 * s), fill=(220, 60, 60, 255), width=max(2, round(5 * s)))
    return img


class Tray:
    def __init__(
        self,
        *,
        language: str,
        on_language: Callable[[str], None],
        on_settings: Callable[[], None],
        on_quit: Callable[[], None],
    ) -> None:
        import pystray

        self._language = language
        self._status = "Starting…"
        self._on_language = on_language
        self._on_quit = on_quit
        self._images = {state: icon_image(state) for state in COLORS}

        def set_lang(code: str) -> Callable:
            def handler(icon: pystray.Icon, item: pystray.MenuItem) -> None:
                self._language = code
                on_language(code)

            return handler

        languages = pystray.Menu(
            *(
                pystray.MenuItem(
                    LANGUAGE_NAMES[c], set_lang(c), checked=lambda _i, c=c: self._language == c, radio=True
                )
                for c in LANGUAGES
            )
        )
        menu = pystray.Menu(
            pystray.MenuItem(lambda _i: self._status, None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Language", languages),
            pystray.MenuItem("Settings…", lambda *_: on_settings()),
            pystray.MenuItem("Quit dictate", lambda *_: self._quit()),
        )
        self.icon = pystray.Icon("dictate", self._images["offline"], "dictate", menu)

    def set_state(self, state: str, status: str) -> None:
        """Safe to call from any thread."""
        self._status = status
        if sys.platform == "darwin":
            # macOS kills the app if a menu-bar item is touched off the main thread: hand it over.
            from PyObjCTools import AppHelper

            AppHelper.callAfter(self._apply, state, status)
        else:
            self._apply(state, status)

    def _apply(self, state: str, status: str) -> None:
        try:
            self.icon.icon = self._images.get(state, self._images["ready"])
            self.icon.title = f"dictate: {status}"
            self.icon.update_menu()
        except Exception:  # the icon may not be running yet
            log.debug("tray update failed", exc_info=True)

    def _quit(self) -> None:
        self._on_quit()
        self.icon.stop()

    def run(self) -> None:
        """Blocks; must be called on the main thread (required on macOS)."""
        self.icon.run()
