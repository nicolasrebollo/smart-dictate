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


# -- one hotkey client per computer, separate start-at-login entries ------------------------------------
def test_second_copy_cannot_take_a_held_lock() -> None:
    from dictate import instance

    first = instance.hold("test-lock-held")
    assert first is not None
    assert instance.hold("test-lock-held") is None
    assert instance.hold("test-lock-other") is not None


@pytest.fixture
def fake_run_key(monkeypatch, tmp_path):
    values: dict[str, str] = {}
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(autostart, "_win_get", values.get)
    monkeypatch.setattr(autostart, "_win_set", values.__setitem__)
    monkeypatch.setattr(autostart, "_win_delete", lambda name: values.pop(name, None))
    monkeypatch.setattr(autostart, "_legacy_windows_script", lambda: tmp_path / "dictate.vbs")
    return values


def _as_app(monkeypatch) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Users\x\AppData\Local\Programs\Dictate\Dictate.exe")


def test_installed_app_moves_the_servers_autostart_aside_instead_of_replacing_it(monkeypatch, fake_run_key) -> None:
    server = r'"C:\src\.venv\Scripts\pythonw.exe" -m dictate.cli pc'
    fake_run_key["Dictate"] = server  # written by an older source checkout
    _as_app(monkeypatch)
    assert not autostart.is_enabled()
    autostart.enable("client")
    assert fake_run_key == {"Dictate (from source)": server, "Dictate": fake_run_key["Dictate"]}
    assert "Dictate.exe" in fake_run_key["Dictate"] and autostart.is_enabled()
    autostart.disable()
    assert fake_run_key == {"Dictate (from source)": server}


def test_source_autostart_leaves_the_installed_apps_alone(monkeypatch, fake_run_key) -> None:
    app = r'"C:\Users\x\AppData\Local\Programs\Dictate\Dictate.exe"'
    fake_run_key["Dictate"] = app
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert not autostart.is_enabled()
    autostart.enable("pc")
    assert fake_run_key["Dictate"] == app and "dictate.cli pc" in fake_run_key["Dictate (from source)"]
    autostart.disable()
    assert fake_run_key == {"Dictate": app}


def test_mac_app_moves_an_old_source_launch_agent_aside(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(autostart.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(autostart.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(autostart.os, "getuid", lambda: 501, raising=False)
    agents = tmp_path / "Library" / "LaunchAgents"
    agents.mkdir(parents=True)
    monkeypatch.delattr(sys, "frozen", raising=False)
    old = autostart.mac_plist_text(["/src/.venv/bin/python", "-m", "dictate.cli", "client"], label=autostart.LABEL)
    (agents / "com.dictate.app.plist").write_text(old, encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/Applications/Dictate.app/Contents/MacOS/Dictate")
    autostart.enable("client")
    moved = (agents / "com.dictate.source.plist").read_text(encoding="utf-8")
    assert "<string>com.dictate.source</string>" in moved and "dictate.cli" in moved
    assert "Dictate.app" in (agents / "com.dictate.app.plist").read_text(encoding="utf-8")


def test_secure_links_use_the_computers_own_trusted_certificates() -> None:
    import ssl

    from dictate.client import tls_context

    assert tls_context("ws://127.0.0.1:8765/ws?token=x") is None  # the PC's own client: no TLS
    ctx = tls_context("wss://pc.tail1234.ts.net/ws?token=x")
    assert isinstance(ctx, ssl.SSLContext) and type(ctx).__module__.startswith("truststore")
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname


def test_online_setup_saves_choices_and_keeps_keys_out_of_the_file(monkeypatch, tmp_path) -> None:
    """Claude for the cleanup (no speech API), so a second service hears you: as a person would click it."""
    from dictate import app, dialogs
    from dictate.cloud import keys, providers
    from dictate.config import load_config

    path = tmp_path / "config.toml"
    cfg = load_config(path)
    choices = iter(["anthropic", "openai"])
    asked: list[str] = []
    monkeypatch.setattr(dialogs, "choose", lambda title, text, options, ok="Continue": next(choices))
    monkeypatch.setattr(dialogs, "message", lambda title, text, buttons=("OK",), default="": "I have my key")
    monkeypatch.setattr(dialogs, "clipboard_text", lambda: "")

    def ask(title, text, default="", ok="OK", *, secret=False):
        assert secret  # keys are typed into a dots field
        asked.append(title)
        return {"Your Claude API key": "sk-ant-api03-test", "Your OpenAI API key": "sk-proj-test"}[title]

    monkeypatch.setattr(dialogs, "ask", ask)
    models = {"anthropic": ["claude-opus-5-5", "claude-haiku-5-5"], "openai": ["gpt-transcribe", "gpt-6-luna"]}
    monkeypatch.setattr(providers, "list_models", lambda p, key, **kw: models[p.id])
    saved: dict[str, str] = {}
    monkeypatch.setattr(keys, "set_key", saved.__setitem__)

    assert app._setup_online(cfg)
    assert asked == ["Your Claude API key", "Your OpenAI API key"]
    assert saved == {"anthropic": "sk-ant-api03-test", "openai": "sk-proj-test"}
    text = path.read_text(encoding="utf-8")
    assert "sk-" not in text  # never in the settings file
    cfg = load_config(path)
    assert cfg.client.mode == "online"
    assert (cfg.online.speech, cfg.online.speech_model) == ("openai", "gpt-transcribe")
    assert (cfg.online.cleanup, cfg.online.cleanup_model) == ("anthropic", "claude-opus-5-5")
