"""Global hotkeys: hold-to-talk, double-tap hands-free, Esc cancel, Shift-at-release = raw.

`GestureRecognizer` is a pure state machine (feed it key events + timestamps, it returns
actions), so it is unit-tested without a keyboard. `HotkeyListener` wires it to pynput's
low-level Windows hook. Hook callbacks only enqueue events: Windows silently removes a
low-level hook whose callback takes too long.
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


# -- key name parsing --------------------------------------------------------------------
VK_NAMES = {
    "ctrl_r": 0xA3,
    "ctrl_l": 0xA2,
    "shift_r": 0xA1,
    "shift_l": 0xA0,
    "alt_r": 0xA5,
    "alt_l": 0xA4,
    "esc": 0x1B,
    "escape": 0x1B,
    "caps_lock": 0x14,
    "pause": 0x13,
    "scroll_lock": 0x91,
    "insert": 0x2D,
    "menu": 0x5D,
    "apps": 0x5D,
    **{f"f{i}": 0x6F + i for i in range(1, 25)},
}
MODIFIER_GROUPS = {"shift": {0xA0, 0xA1, 0x10}, "ctrl": {0xA2, 0xA3, 0x11}, "alt": {0xA4, 0xA5, 0x12}}


def parse_key(name: str) -> set[int]:
    """Key name -> set of Windows virtual-key codes it matches."""
    n = name.strip().lower()
    if n in MODIFIER_GROUPS:
        return set(MODIFIER_GROUPS[n])
    if n in VK_NAMES:
        return {VK_NAMES[n]}
    if n.startswith("vk:"):
        return {int(n[3:], 0)}
    if len(n) == 1:
        return {ord(n.upper())}
    raise ValueError(f"unknown key name {name!r}")


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
    ) -> None:
        self._rec = recognizer
        self._on_event = on_event
        self._ptt = parse_key(ptt)
        self._cancel = parse_key(cancel)
        self._raw = parse_key(raw_modifier)
        self._q: queue.Queue[tuple[str, int, float]] = queue.Queue()
        self._down: set[int] = set()
        self._listener: Any = None
        self._thread = threading.Thread(target=self._run, name="hotkeys", daemon=True)
        self._suppressed: set[int] = set()

    def start(self) -> None:
        from pynput import keyboard

        from dictate.platform_win import INJECTED_TAG

        def win32_filter(msg: int, data: Any) -> bool:
            if data.dwExtraInfo == INJECTED_TAG:
                return False  # our own Ctrl+V / typing: don't treat as user input
            vk = int(data.vkCode)
            is_up = msg in (0x0101, 0x0105)  # WM_KEYUP / WM_SYSKEYUP
            t = time.perf_counter()
            self._q.put(("up" if is_up else "down", vk, t))
            # Swallow Esc while recording so it doesn't also close a dialog in the target app.
            if vk in self._cancel and (self._rec.recording or vk in self._suppressed):
                if is_up:
                    self._suppressed.discard(vk)
                else:
                    self._suppressed.add(vk)
                self._listener.suppress_event()
            return True

        self._listener = keyboard.Listener(win32_event_filter=win32_filter)
        self._listener.start()
        self._thread.start()

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
        self._q.put(("quit", 0, 0.0))

    def _run(self) -> None:
        while True:
            deadline = self._rec.deadline
            timeout = None if deadline is None else max(0.0, deadline - time.perf_counter())
            try:
                kind, vk, t = self._q.get(timeout=timeout)
            except queue.Empty:
                self._emit(self._rec.tick(time.perf_counter()))
                continue
            if kind == "quit":
                return
            try:
                self._emit(self._handle(kind, vk, t))
                self._emit(self._rec.tick(time.perf_counter()))
            except Exception:
                log.exception("hotkey handling failed")
                self._rec.reset()

    def _handle(self, kind: str, vk: int, t: float) -> list[Event]:
        if kind == "down":
            repeat = vk in self._down
            self._down.add(vk)
            if vk in self._raw:
                self._rec.raw_mod(True)
                return []
            if vk in self._ptt:
                return [] if repeat else self._rec.ptt_down(t)
            if vk in self._cancel:
                return [] if repeat else self._rec.cancel_down(t)
            if repeat:
                return []
            events = self._rec.other_key_down(t)
            if events:
                log.debug("other key vk=0x%02X while recording -> %s", vk, events[0].reason)
            return events
        self._down.discard(vk)
        if vk in self._raw:
            self._rec.raw_mod(False)
            return []
        if vk in self._ptt:
            return self._rec.ptt_up(t)
        return []

    def _emit(self, events: list[Event]) -> None:
        for ev in events:
            try:
                self._on_event(ev)
            except Exception:
                log.exception("hotkey action %s failed", ev.action)
