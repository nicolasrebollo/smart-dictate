"""Tray icon (Windows) / menu-bar icon (Mac): shows the state and holds every setting a person needs,
so nobody has to open a settings file.

States: ready (white mic), listening (red), working (amber), offline (grey, crossed out), off (dim).
"""

from __future__ import annotations

import logging
import sys
import threading
from collections.abc import Callable
from typing import Any, Protocol

from PIL import Image, ImageDraw

from dictate import system_audio
from dictate.config import LANGUAGES
from dictate.hotkeys import display_key

log = logging.getLogger(__name__)

COLORS = {
    "ready": (240, 240, 240, 255),
    "listening": (230, 57, 70, 255),
    "working": (245, 166, 35, 255),
    "offline": (130, 130, 130, 255),
    "off": (150, 150, 150, 150),
}
LANGUAGE_NAMES = {"en": "English", "es": "Español", "fr": "Français"}
MIC_LABELS = {"": "Same as the system", system_audio.DEVICE: system_audio.LABEL}
HOTKEYS = ["alt_r", "cmd_r", "fn"] if sys.platform == "darwin" else ["ctrl_r", "alt_r"]


def icon_image(state: str, size: int = 64) -> Image.Image:
    """A microphone. A thin dark outline keeps it visible on light and dark taskbars / menu bars."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = COLORS.get(state, COLORS["ready"])
    s = size / 64
    w = max(2, round(5 * s))
    o = max(1, round(1.5 * s))  # outline width

    def mic(color: tuple[int, ...], grow: float) -> None:
        d.rounded_rectangle((22 * s - grow, 6 * s - grow, 42 * s + grow, 40 * s + grow), radius=10 * s, fill=color)
        d.arc(
            (13 * s - grow, 20 * s - grow, 51 * s + grow, 50 * s + grow), 0, 180, fill=color, width=round(w + grow * 2)
        )
        d.line((32 * s, 50 * s, 32 * s, 58 * s + grow), fill=color, width=round(w + grow * 2))
        d.line((22 * s - grow, 58 * s, 42 * s + grow, 58 * s), fill=color, width=round(w + grow * 2))

    if state != "off":
        mic((30, 30, 30, 200), o)
    mic(c, 0)
    if state == "offline":
        d.line((8 * s, 8 * s, 56 * s, 56 * s), fill=(220, 60, 60, 255), width=w)
    return img


class Controls(Protocol):
    """What the menu drives (the Client implements it)."""

    language: str
    enabled: bool
    hotkey: str
    sounds_on: bool
    live: bool
    can_change_server: bool
    can_reconfigure: bool
    can_update: bool
    label: str

    def set_language(self, code: str) -> None: ...
    def set_enabled(self, on: bool) -> None: ...
    def set_hotkey(self, name: str) -> None: ...
    def set_sounds(self, on: bool) -> None: ...
    def set_live(self, on: bool) -> None: ...
    def microphones(self) -> list[str]: ...
    def microphone(self) -> str: ...
    def set_microphone(self, name: str) -> None: ...
    def autostart_on(self) -> bool: ...
    def set_autostart(self, on: bool) -> None: ...
    def change_server(self) -> None: ...
    def reconfigure(self) -> None: ...
    def check_updates(self) -> None: ...
    def open_notes(self) -> None: ...
    def open_settings(self) -> None: ...
    def stop(self) -> None: ...


class Tray:
    def __init__(self, controls: Controls) -> None:
        import pystray

        self._c = controls
        self._status = "Starting…"
        self._images = {state: icon_image(state) for state in COLORS}
        Item, Menu = pystray.MenuItem, pystray.Menu

        def later(fn: Callable[[], Any]) -> Callable[..., None]:
            """Menu actions run off the tray thread: some show dialogs, which must not freeze the icon."""

            def handler(*_: Any) -> None:
                def run() -> None:
                    try:
                        fn()
                    except Exception:
                        log.exception("menu action failed")
                    self.refresh()

                threading.Thread(target=run, daemon=True).start()

            return handler

        languages = Menu(
            *(
                Item(LANGUAGE_NAMES[code], later(lambda code=code: controls.set_language(code)),
                     checked=lambda _i, code=code: controls.language == code, radio=True)
                for code in LANGUAGES
            )
        )  # fmt: skip
        hotkeys = Menu(
            *(
                Item(display_key(k), later(lambda k=k: controls.set_hotkey(k)),
                     checked=lambda _i, k=k: controls.hotkey == k, radio=True)
                for k in [*HOTKEYS, *([controls.hotkey] if controls.hotkey not in HOTKEYS else [])]
            )
        )  # fmt: skip
        mics = Menu(
            *(
                Item(MIC_LABELS.get(name, name), later(lambda name=name: controls.set_microphone(name)),
                     checked=lambda _i, name=name: controls.microphone() == name, radio=True)
                for name in ["", *controls.microphones()]
            )
        )  # fmt: skip
        autostart = Item(
            "Start when I log in", later(lambda: controls.set_autostart(not controls.autostart_on())),
            checked=lambda _i: controls.autostart_on(),
        )  # fmt: skip
        items = [
            Item(lambda _i: self._status, None, enabled=False),
            *([Item(f"Using: {controls.label}", None, enabled=False)] if controls.label else []),
            Item(lambda _i: "Turn off Dictate" if controls.enabled else "Turn on Dictate",
                 later(lambda: controls.set_enabled(not controls.enabled))),
            Item("Notes…", later(controls.open_notes)),
            Menu.SEPARATOR,
            Item("Language", languages),
            Item("Hotkey", hotkeys),
            Item("Microphone", mics),
            Item("Type as I speak", later(lambda: controls.set_live(not controls.live)),
                 checked=lambda _i: controls.live),
            Item("Sounds", later(lambda: controls.set_sounds(not controls.sounds_on)),
                 checked=lambda _i: controls.sounds_on),
            autostart,
            Menu.SEPARATOR,
        ]  # fmt: skip
        if controls.can_reconfigure:
            items.append(Item("Change how Dictate works…", later(controls.reconfigure)))
        elif controls.can_change_server:
            items.append(Item("Connect to a different PC…", later(controls.change_server)))
        if controls.can_update:
            items.append(Item("Check for updates…", later(controls.check_updates)))
        items += [
            Item("Advanced settings…", later(controls.open_settings)),
            Item("Quit Dictate", lambda *_: self._quit()),
        ]
        self.icon = pystray.Icon("dictate", self._images["offline"], "Dictate", Menu(*items))

    def set_state(self, state: str, status: str) -> None:
        """Safe to call from any thread."""
        self._status = status
        self._call(self._apply, state, status)

    def refresh(self) -> None:
        self._call(self._update_menu)

    def _call(self, fn: Callable[..., None], *args: Any) -> None:
        if sys.platform == "darwin":
            # macOS kills the app if a menu-bar item is touched off the main thread: hand it over.
            from PyObjCTools import AppHelper

            AppHelper.callAfter(fn, *args)
        else:
            fn(*args)

    def _apply(self, state: str, status: str) -> None:
        try:
            self.icon.icon = self._images.get(state, self._images["ready"])
            self.icon.title = f"Dictate: {status}"
            self.icon.update_menu()
        except Exception:  # the icon may not be running yet
            log.debug("tray update failed", exc_info=True)

    def _update_menu(self) -> None:
        try:
            self.icon.update_menu()
        except Exception:
            log.debug("tray menu update failed", exc_info=True)

    def _quit(self) -> None:
        self._c.stop()
        self.icon.stop()

    def quit(self) -> None:
        """Safe to call from any thread."""
        self._call(self._quit)

    def run(self, on_ready: Callable[[], None] | None = None) -> None:
        """Blocks; must be called on the main thread (required on macOS)."""

        def setup(icon: Any) -> None:
            icon.visible = True
            if on_ready is not None:
                threading.Thread(target=on_ready, daemon=True).start()

        self.icon.run(setup=setup)
