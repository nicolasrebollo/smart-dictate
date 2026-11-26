"""Configuration: one small TOML file.

[client] is used on every device (Mac, PC): where the server is, the hotkey, the language.
[server] is only used on the PC that runs the models.

Everything else (speech detection, guardrails, model internals) has sensible defaults and isn't
in the file. Power users can still add those sections ([asr], [vad], [cleanup], [guardrails],
[llm], [streaming], [formatter], [commands]); they're parsed and validated the same way.
"""

from __future__ import annotations

import dataclasses
import logging
import re
import secrets
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, get_type_hints

from dictate import paths

log = logging.getLogger(__name__)

LANGUAGES = ("en", "es", "fr")
# macOS shows an orange "microphone in use" indicator while the mic is open, so there the mic is only
# opened while you hold the key. Elsewhere it stays open so the first syllable is never clipped.
_KEEP_MIC_OPEN = sys.platform != "darwin"

DEFAULT_CONFIG_TOML = """\
# dictate settings.
# [client] matters on every device. [server] only matters on the PC that runs the models.

[client]
server = "ws://127.0.0.1:8765"  # the PC running `dictate server` (with Tailscale: "wss://<pc>.<tailnet>.ts.net")
token = ""                      # shown by `dictate server` on the PC
hotkey = "{hotkey}"{hotkey_pad}# hold to talk, release to insert
hands_free = true               # double-tap the hotkey to talk hands-free; tap it again to stop
cancel = "esc"                  # discard the current recording
raw_modifier = "shift"          # hold while releasing to skip the AI cleanup once
language = "en"                 # en | es | fr (also switchable from the tray icon)
input_device = ""               # "" = default microphone (`dictate devices` lists them)
keep_mic_open = {keep_mic}{keep_pad}# true: never clips your first word, but the OS shows "mic in use" all the time
sounds = true

[server]
port = 8765
token = ""                      # created automatically on first start
cleanup = true                  # AI cleanup pass (punctuation, fillers, numbers, names...)
vocabulary = []                 # names and jargon spelled your way, e.g. ["Okonkwo", "pgvector"]
"""


def default_config_text() -> str:
    hotkey = "alt_r" if sys.platform == "darwin" else "ctrl_r"  # Right Option on Mac, Right Ctrl on PC
    keep = "true" if _KEEP_MIC_OPEN else "false"
    return DEFAULT_CONFIG_TOML.format(
        hotkey=hotkey, hotkey_pad=" " * (21 - len(hotkey)), keep_mic=keep, keep_pad=" " * (16 - len(keep))
    )  # comments at column 33


# -- user-facing sections ----------------------------------------------------------------------------
@dataclass
class ClientConfig:
    server: str = "ws://127.0.0.1:8765"
    token: str = ""
    hotkey: str = "ctrl_r"
    hands_free: bool = True
    cancel: str = "esc"
    raw_modifier: str = "shift"
    language: str = "en"
    input_device: str | int = ""
    keep_mic_open: bool = field(default_factory=lambda: _KEEP_MIC_OPEN)
    sounds: bool = True
    volume: float = 0.25
    preroll_ms: int = 300
    release_tail_ms: int = 60
    tap_max_ms: int = 250
    double_tap_window_ms: int = 400
    restore_clipboard: bool = True
    restore_delay_ms: int = 400


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"  # Tailscale (`tailscale serve`) forwards to this; use 0.0.0.0 for plain LAN
    port: int = 8765
    token: str = ""
    cleanup: bool = True
    vocabulary: list[str] = field(default_factory=list)
    model: str = "gemma-4-e2b"


# -- engine internals (defaults; optional in the file) ---------------------------------------------
@dataclass
class VadConfig:
    threshold: float = 0.5
    pad_ms: int = 250
    max_pause_ms: int = 700
    min_speech_ms: int = 150


@dataclass
class AsrConfig:
    backend: str = "parakeet"
    model: str = "nemo-parakeet-tdt-0.6b-v3"
    device: str = "cuda"
    quantization: str = ""
    language: str = "en"  # per session: set from the client's request
    max_chunk_s: int = 30
    warmup_max_s: int = 12
    keep_warm: bool = True


@dataclass
class CleanupConfig:
    enabled: bool = True
    backend: str = "llama-server"
    model: str = "gemma-4-e2b"
    timeout_ms: int = 2500
    builtin_terms: bool = True
    math_style: str = "unicode"
    incremental: bool = True
    skip_when_clean: bool = True
    skip_short_words: int = 3


@dataclass
class GuardrailConfig:
    min_ratio: float = 0.35
    max_ratio: float = 1.3
    max_novel_fraction: float = 0.35
    min_novel_words: int = 3


@dataclass
class LLMConfig:
    gpu_layers: int = 99
    context: int = 4096
    port: int = 0
    speculative: str = "ngram"
    spec_ngram_n: int = 2
    spec_ngram_m: int = 16
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "gemma4:e2b"


@dataclass
class StreamingConfig:
    segment_pause_ms: int = 450
    min_segment_s: float = 1.5
    max_segment_s: float = 20.0


@dataclass
class FormatterConfig:
    remove_fillers: bool = True
    collapse_repeats: bool = True
    final_period: bool = True
    spoken_identifiers: bool = True
    layout: bool = True  # put a message's greeting and sign-off on their own lines


@dataclass
class CommandsConfig:
    enabled: bool = True
    phrases: dict[str, dict[str, list[str]]] = field(default_factory=dict)


# Spoken commands (see cleanup/commands.py for what each inserts and when a phrase is left as a word).
DEFAULT_PHRASES = {
    "en": {
        "new_line": ["new line", "next line", "line break"],
        "new_paragraph": ["new paragraph", "next paragraph"],
        "scratch_that": ["scratch that"],
        "comma": ["comma"],
        "period": ["period", "full stop"],
        "question_mark": ["question mark"],
        "exclamation_mark": ["exclamation mark", "exclamation point"],
        "colon": ["colon"],
        "semicolon": ["semicolon", "semi colon"],
        "ellipsis": ["dot dot dot", "ellipsis"],
        "open_quote": ["open quote", "open quotes", "begin quote"],
        "close_quote": ["close quote", "close quotes", "end quote", "unquote"],
        "open_paren": ["open paren", "open parenthesis", "open bracket"],
        "close_paren": ["close paren", "close parenthesis", "close bracket"],
    },
    "es": {
        "new_line": ["nueva línea", "nueva linea", "siguiente línea", "siguiente linea"],
        "new_paragraph": ["nuevo párrafo", "nuevo parrafo", "siguiente párrafo", "siguiente parrafo"],
        "period_new_paragraph": ["punto y aparte"],
        "scratch_that": ["borra eso"],
        "comma": ["coma"],
        "period": ["punto y seguido", "punto final", "punto"],
        "semicolon": ["punto y coma"],
        "colon": ["dos puntos"],
        "question_mark": ["cierra interrogación", "cierra interrogacion", "signo de interrogación"],
        "open_question": ["abre interrogación", "abre interrogacion"],
        "exclamation_mark": ["cierra exclamación", "cierra exclamacion", "signo de exclamación"],
        "open_exclamation": ["abre exclamación", "abre exclamacion"],
        "ellipsis": ["puntos suspensivos"],
        "open_quote": ["abre comillas"],
        "close_quote": ["cierra comillas"],
        "open_paren": ["abre paréntesis", "abre parentesis"],
        "close_paren": ["cierra paréntesis", "cierra parentesis"],
    },
    "fr": {
        "new_line": ["nouvelle ligne", "à la ligne", "ligne suivante"],
        "new_paragraph": ["nouveau paragraphe", "paragraphe suivant"],
        "period_new_line": ["point à la ligne"],
        "scratch_that": ["efface ça", "efface ca"],
        "comma": ["virgule"],
        "period": ["point final", "point"],
        "semicolon": ["point-virgule"],
        "colon": ["deux-points"],
        "question_mark": ["point d'interrogation"],
        "exclamation_mark": ["point d'exclamation"],
        "ellipsis": ["points de suspension"],
        "open_quote": ["ouvrez les guillemets", "ouvrir les guillemets", "ouvrez guillemets"],
        "close_quote": ["fermez les guillemets", "fermer les guillemets", "fermez guillemets"],
        "open_paren": ["ouvrez la parenthèse", "ouvrir la parenthèse", "ouvrez parenthèse"],
        "close_paren": ["fermez la parenthèse", "fermer la parenthèse", "fermez parenthèse"],
    },
}


@dataclass
class VocabularyConfig:
    words: list[str] = field(default_factory=list)


@dataclass
class LoggingConfig:
    debug: bool = False
    log_transcripts: bool = False


@dataclass
class Config:
    client: ClientConfig = field(default_factory=ClientConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    asr: AsrConfig = field(default_factory=AsrConfig)
    vad: VadConfig = field(default_factory=VadConfig)
    cleanup: CleanupConfig = field(default_factory=CleanupConfig)
    guardrails: GuardrailConfig = field(default_factory=GuardrailConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    streaming: StreamingConfig = field(default_factory=StreamingConfig)
    formatter: FormatterConfig = field(default_factory=FormatterConfig)
    commands: CommandsConfig = field(default_factory=lambda: CommandsConfig(phrases=dict(DEFAULT_PHRASES)))
    vocabulary: VocabularyConfig = field(default_factory=VocabularyConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    models_dir: Path = field(default_factory=paths.default_models_dir)
    source_path: Path | None = None


class ConfigError(ValueError):
    pass


_SECTIONS: dict[str, type] = {
    "client": ClientConfig,
    "server": ServerConfig,
    "asr": AsrConfig,
    "vad": VadConfig,
    "cleanup": CleanupConfig,
    "guardrails": GuardrailConfig,
    "llm": LLMConfig,
    "streaming": StreamingConfig,
    "formatter": FormatterConfig,
    "logging": LoggingConfig,
}


def _coerce(section: str, key: str, value: Any, expected: Any) -> Any:
    def bad(kind: str) -> ConfigError:
        return ConfigError(f"[{section}] {key} must be {kind}, got {value!r}")

    if expected is bool:
        if not isinstance(value, bool):
            raise bad("true or false")
    elif expected is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise bad("an integer")
    elif expected is float:
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise bad("a number")
        return float(value)
    elif expected is str and not isinstance(value, str):
        raise bad("a string")
    elif expected == list[str] and not (isinstance(value, list) and all(isinstance(v, str) for v in value)):
        raise bad("a list of strings")
    elif expected == str | int and not isinstance(value, str | int):
        raise bad("a string or a number")
    return value


def _build_section(cls: type, section: str, raw: dict[str, Any]) -> Any:
    hints = get_type_hints(cls)
    names = {f.name for f in dataclasses.fields(cls)}
    kwargs: dict[str, Any] = {}
    for key, value in raw.items():
        if key not in names:
            log.warning("config: unknown setting [%s] %s (ignored)", section, key)
            continue
        kwargs[key] = _coerce(section, key, value, hints[key])
    return cls(**kwargs)


def _validate(cfg: Config) -> None:
    c, s = cfg.client, cfg.server
    if c.language not in LANGUAGES:
        raise ConfigError(f"[client] language must be one of {', '.join(LANGUAGES)}, got {c.language!r}")
    if not re.match(r"^wss?://", c.server):
        raise ConfigError(f"[client] server must start with ws:// or wss://, got {c.server!r}")
    if c.hotkey == c.cancel:
        raise ConfigError("[client] hotkey and cancel must be different keys")
    if not 0.0 <= c.volume <= 1.0:
        raise ConfigError("[client] volume must be between 0.0 and 1.0")
    if not 0 < s.port < 65536:
        raise ConfigError("[server] port must be between 1 and 65535")
    if cfg.asr.device not in ("cuda", "cpu"):
        raise ConfigError(f"[asr] device must be 'cuda' or 'cpu', got {cfg.asr.device!r}")
    if cfg.asr.quantization not in ("", "int8"):
        raise ConfigError(f"[asr] quantization must be '' or 'int8', got {cfg.asr.quantization!r}")
    if cfg.cleanup.backend not in ("llama-server", "ollama"):
        raise ConfigError("[cleanup] backend must be 'llama-server' or 'ollama'")
    if cfg.cleanup.math_style not in ("unicode", "ascii"):
        raise ConfigError("[cleanup] math_style must be 'unicode' or 'ascii'")
    if cfg.llm.speculative not in ("ngram", "none"):
        raise ConfigError("[llm] speculative must be 'ngram' or 'none'")
    if not 0 < cfg.guardrails.min_ratio < cfg.guardrails.max_ratio:
        raise ConfigError("[guardrails] need 0 < min_ratio < max_ratio")
    if not 0.0 < cfg.vad.threshold < 1.0:
        raise ConfigError("[vad] threshold must be between 0 and 1")


def parse_config(text: str, source_path: Path | None = None) -> Config:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"config file is not valid TOML: {e}") from e
    cfg = Config(source_path=source_path)
    for name, cls in _SECTIONS.items():
        setattr(cfg, name, _build_section(cls, name, data.get(name, {})))
    cmd = dict(data.get("commands", {}))
    # merged per command, so customizing one phrase list doesn't drop the other defaults
    phrases = {lang: dict(p) for lang, p in DEFAULT_PHRASES.items()}
    for lang, custom in cmd.pop("phrases", {}).items():
        phrases.setdefault(lang, {}).update(custom)
    cfg.commands = _build_section(CommandsConfig, "commands", cmd)
    cfg.commands.phrases = phrases
    if "models_dir" in data:
        cfg.models_dir = Path(data["models_dir"]).expanduser()
    for key in data:
        if key not in {*_SECTIONS, "commands", "models_dir"}:
            log.warning("config: unknown section [%s] (ignored)", key)

    # The short [server] keys drive the engine.
    cfg.cleanup.enabled = cfg.cleanup.enabled and cfg.server.cleanup
    if cfg.server.model:
        cfg.cleanup.model = cfg.server.model
    cfg.vocabulary.words = list(cfg.server.vocabulary)
    cfg.asr.language = cfg.client.language
    _validate(cfg)
    return cfg


def ensure_config_file(path: Path | None = None) -> Path:
    path = path or paths.config_path()
    if path.exists():
        text = path.read_text(encoding="utf-8")
        if "[client]" not in text:  # a config from an older version: keep it aside, start fresh
            old = path.with_name("config.old.toml")
            path.replace(old)
            log.warning("your previous settings were moved to %s (new, simpler format)", old)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(default_config_text(), encoding="utf-8")
        log.info("created config at %s", path)
    return path


def load_config(path: Path | None = None) -> Config:
    path = ensure_config_file(path)
    return parse_config(path.read_text(encoding="utf-8"), source_path=path)


def set_value(path: Path, section: str, key: str, value: str) -> None:
    """Set `key = "value"` inside [section], keeping everything else (comments included) as is."""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    rx = re.compile(rf'^(\s*{re.escape(key)}\s*=\s*)"[^"]*"(.*)$', re.DOTALL)
    in_section, insert_at = False, None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("["):
            if in_section:
                insert_at = i
                break
            in_section = stripped == f"[{section}]"
            continue
        if in_section and (m := rx.match(line)):
            head, rest = f'{m.group(1)}"{value}"', m.group(2)
            if rest.lstrip().startswith("#"):  # keep the comment, aligned like the others when it fits
                rest = " " * max(1, 32 - len(head)) + rest.lstrip()
            lines[i] = head + rest
            path.write_text("".join(lines), encoding="utf-8")
            return
    if in_section:  # section exists but has no such key
        pos = insert_at if insert_at is not None else len(lines)
        if pos == len(lines) and lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.insert(pos, f'{key} = "{value}"\n')
    else:
        lines.append(f'\n[{section}]\n{key} = "{value}"\n')
    path.write_text("".join(lines), encoding="utf-8")


def ensure_server_token(cfg: Config) -> str:
    """The server's shared secret: created on first start and written into the config file."""
    if cfg.server.token:
        return cfg.server.token
    token = secrets.token_urlsafe(18)
    if cfg.source_path is not None:
        set_value(cfg.source_path, "server", "token", token)
    cfg.server.token = token
    return token
