"""macOS keyboard input via a Quartz event tap (listen-only), instead of pynput.

pynput's macOS listener calls the keyboard-layout API (TSM/TIS) from a background thread, which
recent macOS versions treat as fatal ("Python quit unexpectedly"). We only need a few keys
(modifiers, Esc, F-keys) and "some other key was pressed", so we read raw key codes directly and
never touch the layout API.

Needs Accessibility + Input Monitoring permission for the app that runs dictate (e.g. Terminal).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

log = logging.getLogger(__name__)

# Marks events we post ourselves (Cmd+V), so the tap ignores them.
INJECTED_TAG = 0x64696374  # "dict"

# Modifier key code -> (name, device-specific flag bit that is set while that key is down)
MODIFIERS: dict[int, tuple[str, int]] = {
    59: ("ctrl_l", 0x00000001),
    62: ("ctrl_r", 0x00002000),
    56: ("shift_l", 0x00000002),
    60: ("shift_r", 0x00000004),
    55: ("cmd_l", 0x00000008),
    54: ("cmd_r", 0x00000010),
    58: ("alt_l", 0x00000020),
    61: ("alt_r", 0x00000040),
    57: ("caps_lock", 0x00010000),
    63: ("fn", 0x00800000),
}
OTHER_KEYS = {53: "esc", 48: "tab", 49: "space", 105: "f13", 107: "f14", 113: "f15", 106: "f16", 64: "f17", 79: "f18",
              80: "f19", 90: "f20", 122: "f1", 120: "f2", 99: "f3", 118: "f4", 96: "f5", 97: "f6", 98: "f7", 100: "f8",
              101: "f9", 109: "f10", 103: "f11", 111: "f12"}  # fmt: skip


def key_name(keycode: int) -> str:
    if keycode in MODIFIERS:
        return MODIFIERS[keycode][0]
    return OTHER_KEYS.get(keycode, f"vk:{keycode}")


class MacKeyTap:
    """Calls on_key(kind, name) with kind "down"/"up", from its own thread (keep it fast)."""

    def __init__(self, on_key: Callable[[str, str], None]) -> None:
        self._on_key = on_key
        self._loop = None
        self._tap = None
        self._ready = threading.Event()
        self._error: str | None = None
        self._thread = threading.Thread(target=self._run, name="mac-keytap", daemon=True)

    def start(self) -> None:
        self._thread.start()
        self._ready.wait(5)
        if self._error:
            raise PermissionError(self._error)

    def stop(self) -> None:
        if self._loop is not None:
            from CoreFoundation import CFRunLoopStop

            CFRunLoopStop(self._loop)

    def _run(self) -> None:
        import Quartz
        from CoreFoundation import (
            CFMachPortCreateRunLoopSource,
            CFRunLoopAddSource,
            CFRunLoopGetCurrent,
            CFRunLoopRun,
            kCFRunLoopCommonModes,
        )

        mask = (1 << Quartz.kCGEventKeyDown) | (1 << Quartz.kCGEventKeyUp) | (1 << Quartz.kCGEventFlagsChanged)

        def callback(proxy, etype, event, refcon):
            try:
                if etype in (Quartz.kCGEventTapDisabledByTimeout, Quartz.kCGEventTapDisabledByUserInput):
                    Quartz.CGEventTapEnable(self._tap, True)  # macOS switched us off: switch back on
                    return event
                if Quartz.CGEventGetIntegerValueField(event, Quartz.kCGEventSourceUserData) == INJECTED_TAG:
                    return event
                code = int(Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode))
                if etype == Quartz.kCGEventFlagsChanged:
                    if code in MODIFIERS:
                        name, bit = MODIFIERS[code]
                        down = bool(int(Quartz.CGEventGetFlags(event)) & bit)
                        self._on_key("down" if down else "up", name)
                elif etype == Quartz.kCGEventKeyDown:
                    if not Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventAutorepeat):
                        self._on_key("down", key_name(code))
                elif etype == Quartz.kCGEventKeyUp:
                    self._on_key("up", key_name(code))
            except Exception:  # never let an exception escape into the tap
                log.exception("key tap callback failed")
            return event

        self._callback = callback  # keep a reference: the tap holds only a weak one
        self._tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionListenOnly,
            mask,
            callback,
            None,
        )
        if self._tap is None:
            self._error = (
                "macOS didn't allow dictate to see the keyboard. Open System Settings > Privacy & Security, "
                "turn on the app you run dictate from (e.g. Terminal) under BOTH Accessibility and Input "
                "Monitoring, then quit that app completely (Cmd+Q), reopen it and run dictate again."
            )
            self._ready.set()
            return
        source = CFMachPortCreateRunLoopSource(None, self._tap, 0)
        self._loop = CFRunLoopGetCurrent()
        CFRunLoopAddSource(self._loop, source, kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(self._tap, True)
        self._ready.set()
        CFRunLoopRun()


def press_cmd_v() -> None:
    """Post Cmd+V (key code 9 = V), tagged so our own tap ignores it. Only Command is set, even if the
    user is still holding Shift or Option."""
    import Quartz

    for down in (True, False):
        ev = Quartz.CGEventCreateKeyboardEvent(None, 9, down)
        Quartz.CGEventSetFlags(ev, Quartz.kCGEventFlagMaskCommand)
        Quartz.CGEventSetIntegerValueField(ev, Quartz.kCGEventSourceUserData, INJECTED_TAG)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
