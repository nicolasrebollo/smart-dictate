"""The desktop app's pieces that don't need a screen: the setup link, saved settings, start at login,
and turning the hotkey off."""

import sys

import pytest

from dictate import autostart
from dictate.app import configured
from dictate.config import parse_config, set_value
from dictate.hotkeys import Action, GestureRecognizer, HotkeyListener
from dictate.invite import parse_invite


@pytest.mark.parametrize(
    ("link", "expected"),
    [
        ("https://pc.tail1234.ts.net/?t=AbC-12_x", ("wss://pc.tail1234.ts.net", "AbC-12_x")),
        ("  https://pc.tail1234.ts.net/?t=AbC  \n", ("wss://pc.tail1234.ts.net", "AbC")),
        ("https://pc.tail1234.ts.net/#t=AbC", ("wss://pc.tail1234.ts.net", "AbC")),
        ("<https://pc.tail1234.ts.net/?t=AbC>", ("wss://pc.tail1234.ts.net", "AbC")),
        ("Here's the link: https://pc.tail1234.ts.net/?t=AbC.", ("wss://pc.tail1234.ts.net", "AbC")),
        ("wss://pc.tail1234.ts.net/ws?token=AbC", ("wss://pc.tail1234.ts.net", "AbC")),
        ("http://192.168.1.20:8765/?t=AbC", ("ws://192.168.1.20:8765", "AbC")),
    ],
)
def test_invite_links(link: str, expected: tuple[str, str]) -> None:
    assert parse_invite(link) == expected


@pytest.mark.parametrize(
    "text", ["", "pc.tail1234.ts.net", "https://pc.tail1234.ts.net/", "https://pc.ts.net/?t=", "https:///?t=x"]
)
def test_not_invite_links(text: str) -> None:
    assert parse_invite(text) is None


def test_configured_needs_a_remote_server_and_token() -> None:
    assert not configured(parse_config(""))
    assert not configured(parse_config('[client]\nserver = "wss://pc.x.ts.net"\n'))
    assert configured(parse_config('[client]\nserver = "wss://pc.x.ts.net"\ntoken = "abc"\n'))


def test_set_value_writes_booleans_and_quotes(tmp_path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[client]\nsounds = true                    # beeps\ninput_device = ""\n', encoding="utf-8")
    set_value(path, "client", "sounds", False)
    set_value(path, "client", "input_device", 'Mic "USB"')
    text = path.read_text(encoding="utf-8")
    assert "sounds = false" in text and "# beeps" in text
    cfg = parse_config(text)
    assert cfg.client.sounds is False and cfg.client.input_device == 'Mic "USB"'


def test_autostart_starts_the_installed_app_itself(monkeypatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/Applications/Dictate.app/Contents/MacOS/Dictate")
    assert autostart.launch_command("client") == ["/Applications/Dictate.app/Contents/MacOS/Dictate"]
    plist = autostart.mac_plist_text(autostart.launch_command("client"))
    assert "<string>/Applications/Dictate.app/Contents/MacOS/Dictate</string>" in plist
    assert "AssociatedBundleIdentifiers" in plist


def test_autostart_from_source_runs_the_module(monkeypatch) -> None:
    monkeypatch.delattr(sys, "frozen", raising=False)
    cmd = autostart.launch_command("pc")
    assert cmd[1:] == ["-m", "dictate.cli", "pc"]


def _listener(events: list) -> HotkeyListener:
    return HotkeyListener(GestureRecognizer(), events.append, ptt="ctrl_r")


def test_turned_off_hotkey_does_nothing_and_drops_a_recording() -> None:
    events: list = []
    hk = _listener(events)
    for ev in hk._handle("down", "ctrl_r", 0.0):
        events.append(ev)
    assert [e.action for e in events] == [Action.START]
    hk._control("pause", "")  # turned off mid-recording: the recording is discarded
    assert events[-1].action == Action.DISCARD
    assert hk._handle("up", "ctrl_r", 1.0) == []
    assert hk._handle("down", "ctrl_r", 2.0) == []
    hk._handle("up", "ctrl_r", 2.1)
    hk._control("resume", "")
    assert [e.action for e in hk._handle("down", "ctrl_r", 3.0)] == [Action.START]


def test_hotkey_can_be_changed() -> None:
    hk = _listener([])
    hk._control("ptt", "alt_r")
    assert hk._handle("down", "ctrl_r", 0.0) == []
    assert [e.action for e in hk._handle("down", "alt_gr", 1.0)] == [Action.START]
