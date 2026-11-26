import re

import pytest

from dictate.config import (
    ConfigError,
    default_config_text,
    ensure_server_token,
    load_config,
    parse_config,
    set_value,
)


def test_default_config_parses() -> None:
    cfg = parse_config(default_config_text())
    assert cfg.client.server == "ws://127.0.0.1:8765"
    assert cfg.client.language == "en"
    assert cfg.server.port == 8765 and cfg.server.cleanup
    assert cfg.cleanup.model == "gemma-4-e2b"
    assert cfg.commands.phrases["fr"]["new_line"][:2] == ["nouvelle ligne", "à la ligne"]
    assert cfg.commands.phrases["en"]["comma"] == ["comma"]


def test_custom_phrases_merge_with_defaults() -> None:
    cfg = parse_config('[commands.phrases.en]\nnew_line = ["newline please"]\n')
    assert cfg.commands.phrases["en"]["new_line"] == ["newline please"]
    assert cfg.commands.phrases["en"]["comma"] == ["comma"]  # the other defaults are kept
    assert cfg.commands.phrases["es"]["comma"] == ["coma"]


def test_short_server_keys_drive_the_engine() -> None:
    cfg = parse_config('[client]\nlanguage = "es"\n[server]\ncleanup = false\nvocabulary = ["Okonkwo"]\n')
    assert cfg.cleanup.enabled is False
    assert cfg.vocabulary.words == ["Okonkwo"]
    assert cfg.asr.language == "es"


def test_advanced_sections_still_work() -> None:
    cfg = parse_config('[vad]\nthreshold = 0.6\n[llm]\nspeculative = "none"\n')
    assert cfg.vad.threshold == 0.6 and cfg.llm.speculative == "none"


@pytest.mark.parametrize(
    "bad",
    [
        '[client]\nlanguage = "de"\n',
        '[client]\nserver = "http://pc:8765"\n',
        '[client]\nhotkey = "esc"\n',
        "[server]\nport = 0\n",
        '[server]\nvocabulary = "Okonkwo"\n',
        '[asr]\ndevice = "tpu"\n',
        "not toml at all [",
    ],
)
def test_invalid_config_rejected(bad: str) -> None:
    with pytest.raises(ConfigError):
        parse_config(bad)


def test_unknown_keys_warn_but_load(caplog: pytest.LogCaptureFixture) -> None:
    cfg = parse_config("[client]\nbogus = 1\n[history]\nenabled = true\n")
    assert cfg.client.hotkey == "ctrl_r"
    assert "bogus" in caplog.text and "history" in caplog.text


def test_first_run_creates_file_and_old_format_is_moved_aside(tmp_path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[hotkeys]\npush_to_talk = "ctrl_r"\n', encoding="utf-8")  # pre-M5 format
    cfg = load_config(path)
    assert (tmp_path / "config.old.toml").exists()
    assert "[client]" in path.read_text(encoding="utf-8")
    assert cfg.client.server.startswith("ws://")


def test_server_token_created_once_and_saved(tmp_path) -> None:
    path = tmp_path / "config.toml"
    cfg = load_config(path)
    token = ensure_server_token(cfg)
    assert len(token) > 16
    again = load_config(path)
    assert again.server.token == token
    assert ensure_server_token(again) == token
    text = path.read_text(encoding="utf-8")
    assert "# created automatically on first start" in text  # comment kept


def test_set_value_replaces_or_appends(tmp_path) -> None:
    path = tmp_path / "c.toml"
    path.write_text('[client]\nlanguage = "en"  # note\n\n[server]\nport = 8765\n', encoding="utf-8")
    set_value(path, "client", "language", "fr")
    set_value(path, "server", "token", "abc")
    text = path.read_text(encoding="utf-8")
    assert re.search(r'^language = "fr" +# note$', text, re.M)  # value changed, comment kept
    cfg = parse_config(text)
    assert cfg.client.language == "fr" and cfg.server.token == "abc"
