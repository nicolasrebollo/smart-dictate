"""Global hotkeys: hold-to-talk, double-tap hands-free, Esc cancel, Shift-at-release = raw.

`GestureRecognizer` is a pure state machine (feed it key events + timestamps, it returns
actions), so it is unit-tested without a keyboard. `HotkeyListener` wires it to pynput, which
works on Windows and macOS. Key callbacks only enqueue events: Windows silently removes a
keyboard hook whose callback takes too long.
"""

from __future__ import annotations

import enum
import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)


class Action(enum.Enum):
    START = "start"  # begin recording
    STOP = "stop"  # finish recording and insert
    CANCEL = "cancel"  # discard (user pressed Esc)
    DISCARD = "discard"  # discard silently (stray tap, or PTT used as a normal modifier)
    HANDS_FREE = "hands_free"  # recording continues hands-free


@dataclass(frozen=True)
class Event:
    action: Action
    raw: bool = False
    reason: str = ""


class _S(enum.Enum):
    IDLE = 0
    HOLD = 1  # PTT down, recording
    TAP_WAIT = 2  # quick tap released; waiting to see if a second tap follows
    HF_ARMING = 3  # second tap is down; hands-free starts, waiting for its release
    HANDS_FREE = 4  # recording, key up
    HF_STOPPING = 5  # stop tap is down; stop on its release
    WAIT_RELEASE = 6  # recording ended/cancelled while PTT still held; ignore until release


class GestureRecognizer:
    def __init__(self, *, tap_max_s: float = 0.25, double_tap_window_s: float = 0.4, hands_free: bool = True):
        self.tap_max_s = tap_max_s
        self.double_tap_window_s = double_tap_window_s
        self.hands_free_enabled = hands_free
        self._state = _S.IDLE
        self._t_down = 0.0
        self._deadline: float | None = None
        self._raw_mod_down = False

    @property
    def recording(self) -> bool:
        return self._state in (_S.HOLD, _S.TAP_WAIT, _S.HF_ARMING, _S.HANDS_FREE, _S.HF_STOPPING)

    @property
    def deadline(self) -> float | None:
        return self._deadline

    # Each handler returns the actions to perform (usually 0 or 1).
    def ptt_down(self, t: float) -> list[Event]:
        s = self._state
        if s == _S.IDLE:
            self._state, self._t_down = _S.HOLD, t
            return [Event(Action.START)]
        if s == _S.TAP_WAIT:
            self._deadline = None
            self._state = _S.HF_ARMING
            return [Event(Action.HANDS_FREE)]
        if s == _S.HANDS_FREE:
            self._state = _S.HF_STOPPING
            return []
        return []  # key-repeat while held, etc.

    def ptt_up(self, t: float) -> list[Event]:
        s = self._state
        if s == _S.HOLD:
            if self.hands_free_enabled and (t - self._t_down) < self.tap_max_s:
                self._state = _S.TAP_WAIT
                self._deadline = t + self.double_tap_window_s
                return []
            self._state = _S.IDLE
            return [Event(Action.STOP, raw=self._raw_mod_down)]
        if s == _S.HF_ARMING:
            self._state = _S.HANDS_FREE
            return []
        if s == _S.HF_STOPPING:
            self._state = _S.IDLE
            return [Event(Action.STOP, raw=self._raw_mod_down)]
        if s == _S.WAIT_RELEASE:
            self._state = _S.IDLE
        return []

    def cancel_down(self, t: float) -> list[Event]:
        if not self.recording:
            return []
        ptt_held = self._state in (_S.HOLD, _S.HF_ARMING, _S.HF_STOPPING)
        self._state = _S.WAIT_RELEASE if ptt_held else _S.IDLE
        self._deadline = None
        return [Event(Action.CANCEL, reason="cancel key")]

    def raw_mod(self, down: bool) -> None:
        self._raw_mod_down = down

    def other_key_down(self, t: float) -> list[Event]:
        """Any other key while PTT is held means the user is using it as a normal modifier
        (e.g. Right Ctrl + C), so drop the recording silently."""
        if self._state == _S.HOLD:
            self._state = _S.WAIT_RELEASE
            return [Event(Action.DISCARD, reason="chord")]
        if self._state == _S.TAP_WAIT:
            self._state, self._deadline = _S.IDLE, None
            return [Event(Action.DISCARD, reason="tap")]
        return []

    def tick(self, t: float) -> list[Event]:
        if self._state == _S.TAP_WAIT and self._deadline is not None and t >= self._deadline:
            self._state, self._deadline = _S.IDLE, None
            return [Event(Action.DISCARD, reason="tap")]
        return []

    def reset(self) -> None:
        self._state, self._deadline = _S.IDLE, None


# -- key names ---------------------------------------------------------------------------
def parse_key(name: str) -> frozenset[str]:
    """Config key name -> the pynput key names it matches ("shift" matches either Shift)."""
    n = name.strip().lower()
    groups = {
        "shift": {"shift", "shift_l", "shift_r"},
        "ctrl": {"ctrl", "ctrl_l", "ctrl_r"},
        "alt": {"alt", "alt_l", "alt_r", "alt_gr"},
        "option": {"alt", "alt_l", "alt_r"},
        "cmd": {"cmd", "cmd_l", "cmd_r"},
        "alt_r": {"alt_r", "alt_gr"},  # Windows reports Right Alt as AltGr on many layouts
        "option_r": {"alt_r"},
        "escape": {"esc"},
    }
    if n in groups:
        return frozenset(groups[n])
    known = {"ctrl_l", "ctrl_r", "shift_l", "shift_r", "alt_l", "cmd_l", "cmd_r", "esc", "caps_lock", "fn",
             "pause", "scroll_lock", "insert", "menu", "space", "tab", *(f"f{i}" for i in range(1, 21))}  # fmt: skip
    if n in known:
        return frozenset({n})
    if len(n) == 1:
        return frozenset({f"char:{n}"})
    raise ValueError(f"unknown key name {name!r}")


def key_name(key: Any) -> str:
    """pynput key object -> name used by parse_key."""
    name = getattr(key, "name", None)
    if name:
        return str(name)
    char = getattr(key, "char", None)
    return f"char:{char.lower()}" if char else f"vk:{getattr(key, 'vk', '?')}"


class HotkeyListener:
    """Feeds pynput keyboard events into a GestureRecognizer on its own thread."""

    def __init__(
        self,
        recognizer: GestureRecognizer,
        on_event: Callable[[Event], None],
        *,
        ptt: str = "ctrl_r",
        cancel: str = "esc",
        raw_modifier: str = "shift",
        is_injecting: Callable[[], bool] = lambda: False,
    ) -> None:
        self._rec = recognizer
        self._on_event = on_event
        self._ptt = parse_key(ptt)
        self._cancel = parse_key(cancel)
        self._raw = parse_key(raw_modifier)
        self._is_injecting = is_injecting  # our own simulated Ctrl/Cmd+V must not look like typing
        self._q: queue.Queue[tuple[str, str, float]] = queue.Queue()
        self._down: set[str] = set()
        self._listener: Any = None
        self._thread = threading.Thread(target=self._run, name="hotkeys", daemon=True)
        self._esc_suppressed = False

    def start(self) -> None:
        import sys

        if sys.platform == "darwin":  # pynput's macOS listener can crash Python on recent macOS
            from dictate.hotkeys_mac import MacKeyTap

            self._listener = MacKeyTap(lambda kind, name: self._q.put((kind, name, time.perf_counter())))
            self._listener.start()  # raises PermissionError with instructions if macOS refuses
            self._thread.start()
            return

        from pynput import keyboard

        def on_press(key: Any) -> None:
            if not self._is_injecting():
                self._q.put(("down", key_name(key), time.perf_counter()))

        def on_release(key: Any) -> None:
            if not self._is_injecting():
                self._q.put(("up", key_name(key), time.perf_counter()))

        kwargs: dict[str, Any] = {}
        if sys.platform == "win32":
            from dictate.platform_win import INJECTED_TAG

            def win32_filter(msg: int, data: Any) -> bool:
                # Ignore only our own synthetic keys (Ctrl+V), not other tools' (AutoHotkey, PowerToys remaps).
                if data.dwExtraInfo == INJECTED_TAG:
                    return False
                # Swallow Esc while recording so it doesn't also close a dialog in the target app.
                if data.vkCode == 0x1B and "esc" in self._cancel and (self._rec.recording or self._esc_suppressed):
                    is_up = msg in (0x0101, 0x0105)
                    self._q.put(("up" if is_up else "down", "esc", time.perf_counter()))
                    self._esc_suppressed = not is_up
                    self._listener.suppress_event()
                return True

            kwargs["win32_event_filter"] = win32_filter
        self._listener = keyboard.Listener(on_press=on_press, on_release=on_release, **kwargs)
        self._listener.start()
        self._thread.start()

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
        self._q.put(("quit", "", 0.0))

    def _run(self) -> None:
        while True:
            deadline = self._rec.deadline
            timeout = None if deadline is None else max(0.0, deadline - time.perf_counter())
            try:
                kind, name, t = self._q.get(timeout=timeout)
            except queue.Empty:
                self._emit(self._rec.tick(time.perf_counter()))
                continue
            if kind == "quit":
                return
            try:
                self._emit(self._handle(kind, name, t))
                self._emit(self._rec.tick(time.perf_counter()))
            except Exception:
                log.exception("hotkey handling failed")
                self._rec.reset()

    def _handle(self, kind: str, name: str, t: float) -> list[Event]:
        if kind == "down":
            repeat = name in self._down
            self._down.add(name)
            if repeat:
                return []
            if name in self._raw:
                self._rec.raw_mod(True)
                return []
            if name in self._ptt:
                return self._rec.ptt_down(t)
            if name in self._cancel:
                return self._rec.cancel_down(t)
            return self._rec.other_key_down(t)
        self._down.discard(name)
        if name in self._raw:
            self._rec.raw_mod(False)
            return []
        if name in self._ptt:
            return self._rec.ptt_up(t)
        return []

    def _emit(self, events: list[Event]) -> None:
        for ev in events:
            try:
                self._on_event(ev)
            except Exception:
                log.exception("hotkey action %s failed", ev.action)


def display_key(name: str) -> str:
    """Config key name -> what's printed on the keyboard."""
    import sys

    mac = sys.platform == "darwin"
    names = {
        "alt_r": "Right Option (⌥)" if mac else "Right Alt", "alt_l": "Left Option (⌥)" if mac else "Left Alt",
        "ctrl_r": "Right Control" if mac else "Right Ctrl", "ctrl_l": "Left Control" if mac else "Left Ctrl",
        "cmd_r": "Right Command (⌘)", "cmd_l": "Left Command (⌘)", "shift": "Shift", "esc": "Esc", "fn": "Fn",
    }  # fmt: skip
    return names.get(name.strip().lower(), name.upper() if len(name) <= 3 else name)
