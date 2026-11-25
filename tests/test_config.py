import pytest

from dictate.config import DEFAULT_CONFIG_TOML, ConfigError, ConfigWatcher, load_config, parse_config


def test_default_config_parses() -> None:
    cfg = parse_config(DEFAULT_CONFIG_TOML)
    assert cfg.hotkeys.push_to_talk == "ctrl_r"
    assert cfg.asr.language == "en"
    assert cfg.commands.phrases["fr"]["new_line"] == ["nouvelle ligne", "à la ligne"]
    assert cfg.replacements == []


def test_empty_file_gives_defaults() -> None:
    cfg = parse_config("")
    assert cfg.audio.preroll_ms == 300
    assert "en" in cfg.commands.phrases


def test_replacements_simple_and_table() -> None:
    cfg = parse_config('[replacements]\n"jason" = "JSON"\n"x" = { to = "Y", case_sensitive = true }\n')
    assert [(r.source, r.to, r.case_sensitive) for r in cfg.replacements] == [
        ("jason", "JSON", False),
        ("x", "Y", True),
    ]


def test_input_device_index() -> None:
    assert parse_config("[audio]\ninput_device = 3\n").audio.input_device == 3


@pytest.mark.parametrize(
    "bad",
    [
        '[asr]\nlanguage = "de"\n',
        '[asr]\ndevice = "tpu"\n',
        '[audio]\npreroll_ms = "long"\n',
        "[feedback]\nvolume = 2.0\n",
        '[hotkeys]\npush_to_talk = "esc"\n',
        '[replacements]\n"a" = 3\n',
        "not toml at all [",
    ],
)
def test_invalid_config_rejected(bad: str) -> None:
    with pytest.raises(ConfigError):
        parse_config(bad)


def test_unknown_keys_warn_but_load(caplog: pytest.LogCaptureFixture) -> None:
    cfg = parse_config("[audio]\nbogus = 1\n[nope]\nx = 1\n")
    assert cfg.audio.preroll_ms == 300
    assert "bogus" in caplog.text and "nope" in caplog.text


def test_first_run_creates_file_and_hot_reload(tmp_path) -> None:
    path = tmp_path / "config.toml"
    cfg = load_config(path)
    assert path.exists()
    watcher = ConfigWatcher(cfg)
    assert watcher.maybe_reload() is False
    text = path.read_text(encoding="utf-8").replace('language = "en"', 'language = "es"')
    text = text.replace("[replacements]", '[replacements]\n"jason" = "JSON"')
    path.write_text(text, encoding="utf-8")
    import os

    os.utime(path, (1, 2_000_000_000))
    assert watcher.maybe_reload() is True
    assert cfg.asr.language == "es"
    assert cfg.replacements[0].to == "JSON"


def test_old_config_gets_new_sections_appended_without_touching_settings(tmp_path) -> None:
    from dictate.config import upgrade_config_file

    path = tmp_path / "config.toml"
    path.write_text('[asr]\nlanguage = "fr"\n\n[replacements]\n"jason" = "JSON"\n', encoding="utf-8")
    added = upgrade_config_file(path)
    assert "cleanup" in added and "guardrails" in added and "asr" not in added
    cfg = load_config(path)
    assert cfg.asr.language == "fr"
    assert cfg.replacements[0].to == "JSON"
    assert cfg.cleanup.model == "gemma-4-e2b"
    assert upgrade_config_file(path) == []  # idempotent
