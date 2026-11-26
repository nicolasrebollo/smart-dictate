import pytest

from dictate.hotkeys import Action, GestureRecognizer, parse_key


def acts(events) -> list[Action]:
    return [e.action for e in events]


@pytest.fixture
def g() -> GestureRecognizer:
    return GestureRecognizer(tap_max_s=0.25, double_tap_window_s=0.4, hands_free=True)


def test_hold_to_talk(g: GestureRecognizer) -> None:
    assert acts(g.ptt_down(0.0)) == [Action.START]
    assert g.recording
    out = g.ptt_up(2.0)
    assert acts(out) == [Action.STOP] and out[0].raw is False
    assert not g.recording


def test_raw_modifier_at_release(g: GestureRecognizer) -> None:
    g.ptt_down(0.0)
    g.raw_mod(True)
    assert g.ptt_up(1.0)[0].raw is True


def test_single_tap_is_discarded_after_window(g: GestureRecognizer) -> None:
    g.ptt_down(0.0)
    assert g.ptt_up(0.1) == []
    assert g.tick(0.3) == []
    assert acts(g.tick(0.51)) == [Action.DISCARD]
    assert not g.recording


def test_double_tap_hands_free_then_tap_to_stop(g: GestureRecognizer) -> None:
    g.ptt_down(0.0)
    g.ptt_up(0.1)
    assert acts(g.ptt_down(0.3)) == [Action.HANDS_FREE]
    assert g.ptt_up(0.35) == []
    assert g.tick(5.0) == []  # no timeout while hands-free
    assert g.recording
    assert g.ptt_down(10.0) == []
    assert acts(g.ptt_up(10.1)) == [Action.STOP]
    assert not g.recording


def test_cancel_while_holding_waits_for_release(g: GestureRecognizer) -> None:
    g.ptt_down(0.0)
    assert acts(g.cancel_down(0.5)) == [Action.CANCEL]
    assert not g.recording
    assert g.ptt_up(1.0) == []  # release after cancel does nothing
    assert acts(g.ptt_down(2.0)) == [Action.START]


def test_cancel_in_hands_free(g: GestureRecognizer) -> None:
    g.ptt_down(0.0)
    g.ptt_up(0.1)
    g.ptt_down(0.2)
    g.ptt_up(0.25)
    assert acts(g.cancel_down(3.0)) == [Action.CANCEL]
    assert acts(g.ptt_down(4.0)) == [Action.START]


def test_cancel_when_idle_does_nothing(g: GestureRecognizer) -> None:
    assert g.cancel_down(0.0) == []


def test_chord_discards_silently(g: GestureRecognizer) -> None:
    g.ptt_down(0.0)
    out = g.other_key_down(0.1)  # e.g. Right Ctrl + C
    assert acts(out) == [Action.DISCARD] and out[0].reason == "chord"
    assert g.ptt_up(0.2) == []


def test_other_keys_ignored_in_hands_free(g: GestureRecognizer) -> None:
    g.ptt_down(0.0)
    g.ptt_up(0.1)
    g.ptt_down(0.2)
    g.ptt_up(0.25)
    assert g.other_key_down(1.0) == []
    assert g.recording


def test_hands_free_disabled_short_press_still_stops() -> None:
    g = GestureRecognizer(hands_free=False)
    g.ptt_down(0.0)
    assert acts(g.ptt_up(0.1)) == [Action.STOP]


def test_key_repeat_ignored(g: GestureRecognizer) -> None:
    g.ptt_down(0.0)
    assert g.ptt_down(0.05) == []


def test_parse_key() -> None:
    assert parse_key("ctrl_r") == {"ctrl_r"}
    assert parse_key("alt_r") == {"alt_r", "alt_gr"}
    assert parse_key("shift") == {"shift", "shift_l", "shift_r"}
    assert parse_key("F13") == {"f13"}
    assert parse_key("x") == {"char:x"}
    with pytest.raises(ValueError):
        parse_key("hyper")


def test_listener_maps_events_to_gestures() -> None:
    from dictate.hotkeys import HotkeyListener

    seen = []
    listener = HotkeyListener(GestureRecognizer(), seen.append, ptt="ctrl_r", cancel="esc", raw_modifier="shift")
    assert [e.action for e in listener._handle("down", "ctrl_r", 0.0)] == [Action.START]
    assert listener._handle("down", "ctrl_r", 0.05) == []  # auto-repeat
    listener._handle("down", "shift_l", 0.5)
    out = listener._handle("up", "ctrl_r", 1.0)
    assert [e.action for e in out] == [Action.STOP] and out[0].raw is True
