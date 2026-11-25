"""Configuration: one TOML file with commented defaults, validated into dataclasses.

The file is created on first run at `paths.config_path()`. Sections that are cheap to
change (formatter, replacements, commands, vocabulary, insert, feedback) are hot-reloaded
before each utterance; model/hotkey/audio settings need a restart.
"""

from __future__ import annotations

import dataclasses
import logging
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, get_type_hints

from dictate import paths

log = logging.getLogger(__name__)

LANGUAGES = ("en", "es", "fr")

DEFAULT_CONFIG_TOML = """\
# dictate configuration. Edit and save; formatter, replacements, commands, vocabulary,
# insert and feedback settings reload automatically. Other changes need a restart.

[hotkeys]
# Key names: ctrl_r, ctrl_l, alt_r, shift_r, f13..f24, caps_lock, or "vk:0xA3".
# Avoid Alt alone: tapping Alt focuses the menu bar in many Windows apps.
push_to_talk = "ctrl_r"       # hold to dictate, release to insert
hands_free = true             # double-tap push_to_talk to start; tap it again to stop
raw_modifier = "shift"        # hold Shift while releasing to skip AI cleanup (raw text)
cancel = "esc"                # discard the current recording
tap_max_ms = 250              # presses shorter than this count as taps
double_tap_window_ms = 400    # max gap between the two taps of a double-tap

[audio]
input_device = ""             # "" = system default. Name substring or index (see `dictate devices`)
always_open = true            # keep the mic stream open so the first syllable isn't clipped
preroll_ms = 300              # audio kept from just before the key press
release_tail_ms = 60          # keep recording this long after release (catches the last syllable)
max_record_s = 600            # hard cap on one recording

[vad]
threshold = 0.5               # Silero speech probability threshold
pad_ms = 250                  # silence kept around detected speech
max_pause_ms = 700            # internal pauses longer than this are shortened to this
min_speech_ms = 150           # less speech than this = nothing was said

[asr]
backend = "parakeet"
model = "nemo-parakeet-tdt-0.6b-v3"
device = "cuda"               # "cuda" (fast, ~3 GB VRAM) or "cpu" (no VRAM, slower on long audio)
quantization = ""             # "" = full precision, "int8" = smaller/faster on CPU
language = "en"               # en | es | fr (used for formatting, commands and cleanup)
max_chunk_s = 30              # long recordings are split at pauses into chunks this long
warmup_max_s = 12             # pre-compile audio lengths up to this at startup
keep_warm = true              # CUDA only: keep GPU clocks up while you speak (~2.5x faster release)

[cleanup]
enabled = true                # AI cleanup pass (hold Shift at release to skip it once)
backend = "llama-server"      # "llama-server" (bundled llama.cpp, fastest) or "ollama"
model = "gemma-4-e2b"         # see `dictate models`; download others with `dictate setup --llm NAME`
timeout_ms = 2500             # give up and insert the rule-formatted text after this long
builtin_terms = true          # fix casing of well-known software/companies (GitHub, PostgreSQL, iPhone...)
math_style = "unicode"        # "unicode" (x² + 2x ≥ 1) or "ascii" (x^2 + 2x >= 1)
incremental = true            # clean finished sentences while you're still talking (faster release)
skip_when_clean = true        # don't call the model for text that's already clean (rules only)
skip_short_words = 3          # utterances this short skip the model unless they contain numbers/symbols

[guardrails]
# If the AI output looks wrong it is thrown away and the rule-formatted transcript is inserted.
min_ratio = 0.35              # cleaned/raw word-count ratio band
max_ratio = 1.3
max_novel_fraction = 0.35     # share of output words that never appeared in what you said...
min_novel_words = 3           # ...and at least this many of them, before rejecting

[llm]
gpu_layers = 99               # layers offloaded to the GPU (0 = CPU only)
context = 4096
port = 0                      # 0 = pick a free localhost port
speculative = "ngram"         # "ngram": draft tokens by copying from the transcript (~3x faster cleanup), or "none"
spec_ngram_n = 2              # n-gram lookup length
spec_ngram_m = 16             # max tokens drafted per step
ollama_url = "http://127.0.0.1:11434"
ollama_model = "qwen3.5:2b"

[streaming]
enabled = true                # transcribe finished phrases while you speak
segment_pause_ms = 450        # a pause this long ends a phrase
min_segment_s = 1.5
max_segment_s = 20

[preview]
enabled = true                # live transcript overlay while recording (never takes focus)
interval_ms = 400             # how often the open phrase is re-transcribed for the preview
position = "bottom"           # "bottom" or "top" of the screen with the mouse cursor
width = 760                   # pixels
font_size = 15
show_result_ms = 900          # how long the cleaned text stays visible after inserting

[formatter]
remove_fillers = true         # um, uh, euh, eh... (conservative list)
collapse_repeats = true       # "I I think" -> "I think"
final_period = true           # add a period if the text doesn't end with punctuation
spoken_identifiers = true     # "snake case user id" -> user_id, "dash dash verbose" -> --verbose

[commands]
enabled = true                # spoken "new line", "new paragraph", "scratch that"

[commands.phrases.en]
new_line = ["new line"]
new_paragraph = ["new paragraph"]
scratch_that = ["scratch that"]

[commands.phrases.es]
new_line = ["nueva línea", "nueva linea"]
new_paragraph = ["nuevo párrafo", "nuevo parrafo"]
scratch_that = ["borra eso"]

[commands.phrases.fr]
new_line = ["nouvelle ligne", "à la ligne"]
new_paragraph = ["nouveau paragraphe"]
scratch_that = ["efface ça", "efface ca"]

[vocabulary]
# Names, jargon and acronyms spelled the way you want them.
words = []

[replacements]
# Applied last, whole-word, case-insensitive. Use a table for case-sensitive matching:
#   "jason" = "JSON"
#   "dictate" = { to = "Dictate", case_sensitive = true }

[insert]
method = "paste"              # "paste" (fast) or "type" (simulated typing)
restore_clipboard = true      # put your previous clipboard back after pasting
restore_delay_ms = 400        # wait before restoring (slow apps read the clipboard late)
type_apps = []                # exe names that always get typed instead of pasted, e.g. ["putty.exe"]
newline_key = "shift+enter"   # how "type" inserts line breaks ("enter" sends messages in chat apps)

[feedback]
sounds = true
volume = 0.25                 # 0.0 - 1.0

[logging]
debug = false
log_transcripts = false       # never on by default: logs contain timings only

[privacy]
strict_offline = true         # refuse any non-localhost network connection at runtime
"""


@dataclass
class HotkeyConfig:
    push_to_talk: str = "ctrl_r"
    hands_free: bool = True
    raw_modifier: str = "shift"
    cancel: str = "esc"
    tap_max_ms: int = 250
    double_tap_window_ms: int = 400


@dataclass
class AudioConfig:
    input_device: str | int = ""
    always_open: bool = True
    preroll_ms: int = 300
    release_tail_ms: int = 60
    max_record_s: int = 600


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
    language: str = "en"
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
class StreamingConfig:
    enabled: bool = True
    segment_pause_ms: int = 450
    min_segment_s: float = 1.5
    max_segment_s: float = 20.0


@dataclass
class PreviewConfig:
    enabled: bool = True
    interval_ms: int = 400
    position: str = "bottom"
    width: int = 760
    font_size: int = 15
    show_result_ms: int = 900


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
    ollama_model: str = "qwen3.5:2b"


@dataclass
class FormatterConfig:
    remove_fillers: bool = True
    collapse_repeats: bool = True
    final_period: bool = True
    spoken_identifiers: bool = True


@dataclass
class CommandsConfig:
    enabled: bool = True
    phrases: dict[str, dict[str, list[str]]] = field(default_factory=dict)


@dataclass
class VocabularyConfig:
    words: list[str] = field(default_factory=list)


@dataclass
class Replacement:
    source: str
    to: str
    case_sensitive: bool = False


@dataclass
class InsertConfig:
    method: str = "paste"
    restore_clipboard: bool = True
    restore_delay_ms: int = 400
    type_apps: list[str] = field(default_factory=list)
    newline_key: str = "shift+enter"


@dataclass
class FeedbackConfig:
    sounds: bool = True
    volume: float = 0.25


@dataclass
class LoggingConfig:
    debug: bool = False
    log_transcripts: bool = False


@dataclass
class PrivacyConfig:
    strict_offline: bool = True


@dataclass
class Config:
    hotkeys: HotkeyConfig = field(default_factory=HotkeyConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    vad: VadConfig = field(default_factory=VadConfig)
    asr: AsrConfig = field(default_factory=AsrConfig)
    formatter: FormatterConfig = field(default_factory=FormatterConfig)
    commands: CommandsConfig = field(default_factory=CommandsConfig)
    vocabulary: VocabularyConfig = field(default_factory=VocabularyConfig)
    replacements: list[Replacement] = field(default_factory=list)
    insert: InsertConfig = field(default_factory=InsertConfig)
    feedback: FeedbackConfig = field(default_factory=FeedbackConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    privacy: PrivacyConfig = field(default_factory=PrivacyConfig)
    cleanup: CleanupConfig = field(default_factory=CleanupConfig)
    guardrails: GuardrailConfig = field(default_factory=GuardrailConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    streaming: StreamingConfig = field(default_factory=StreamingConfig)
    preview: PreviewConfig = field(default_factory=PreviewConfig)
    models_dir: Path = field(default_factory=paths.default_models_dir)
    source_path: Path | None = None


class ConfigError(ValueError):
    pass


def _coerce(section: str, key: str, value: Any, expected: Any) -> Any:
    """Light type validation for scalar settings (TOML already gives us typed values)."""
    if expected is bool:
        if not isinstance(value, bool):
            raise ConfigError(f"[{section}] {key} must be true or false, got {value!r}")
        return value
    if expected is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(f"[{section}] {key} must be an integer, got {value!r}")
        return value
    if expected is float:
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ConfigError(f"[{section}] {key} must be a number, got {value!r}")
        return float(value)
    if expected is str:
        if not isinstance(value, str):
            raise ConfigError(f"[{section}] {key} must be a string, got {value!r}")
        return value
    return value


def _build_section(cls: type, section: str, raw: dict[str, Any]) -> Any:
    hints = get_type_hints(cls)
    kwargs: dict[str, Any] = {}
    names = {f.name for f in dataclasses.fields(cls)}
    for key, value in raw.items():
        if key not in names:
            log.warning("config: unknown setting [%s] %s (ignored)", section, key)
            continue
        kwargs[key] = _coerce(section, key, value, hints[key])
    return cls(**kwargs)


def _parse_replacements(raw: dict[str, Any]) -> list[Replacement]:
    out: list[Replacement] = []
    for source, spec in raw.items():
        if isinstance(spec, str):
            out.append(Replacement(source=source, to=spec))
        elif isinstance(spec, dict) and isinstance(spec.get("to"), str):
            out.append(Replacement(source=source, to=spec["to"], case_sensitive=bool(spec.get("case_sensitive"))))
        else:
            raise ConfigError(f'[replacements] {source!r} must be a string or {{ to = "..." }}')
    return out


def _validate(cfg: Config) -> None:
    if cfg.asr.language not in LANGUAGES:
        raise ConfigError(f"[asr] language must be one of {', '.join(LANGUAGES)}, got {cfg.asr.language!r}")
    if cfg.asr.device not in ("cuda", "cpu"):
        raise ConfigError(f"[asr] device must be 'cuda' or 'cpu', got {cfg.asr.device!r}")
    if cfg.asr.quantization not in ("", "int8"):
        raise ConfigError(f"[asr] quantization must be '' or 'int8', got {cfg.asr.quantization!r}")
    if cfg.insert.method not in ("paste", "type"):
        raise ConfigError(f"[insert] method must be 'paste' or 'type', got {cfg.insert.method!r}")
    if cfg.insert.newline_key not in ("enter", "shift+enter"):
        raise ConfigError("[insert] newline_key must be 'enter' or 'shift+enter'")
    if not 0.0 <= cfg.feedback.volume <= 1.0:
        raise ConfigError("[feedback] volume must be between 0.0 and 1.0")
    if not 0.0 < cfg.vad.threshold < 1.0:
        raise ConfigError("[vad] threshold must be between 0 and 1")
    if cfg.cleanup.backend not in ("llama-server", "ollama"):
        raise ConfigError("[cleanup] backend must be 'llama-server' or 'ollama'")
    if cfg.llm.speculative not in ("ngram", "none"):
        raise ConfigError("[llm] speculative must be 'ngram' or 'none'")
    if cfg.preview.position not in ("bottom", "top"):
        raise ConfigError("[preview] position must be 'bottom' or 'top'")
    if cfg.cleanup.math_style not in ("unicode", "ascii"):
        raise ConfigError("[cleanup] math_style must be 'unicode' or 'ascii'")
    if not 0 < cfg.guardrails.min_ratio < cfg.guardrails.max_ratio:
        raise ConfigError("[guardrails] need 0 < min_ratio < max_ratio")
    if cfg.hotkeys.push_to_talk == cfg.hotkeys.cancel:
        raise ConfigError("[hotkeys] push_to_talk and cancel must be different keys")


def parse_config(text: str, source_path: Path | None = None) -> Config:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"config file is not valid TOML: {e}") from e

    defaults = tomllib.loads(DEFAULT_CONFIG_TOML)
    cfg = Config(source_path=source_path)
    simple = {
        "hotkeys": HotkeyConfig,
        "audio": AudioConfig,
        "vad": VadConfig,
        "asr": AsrConfig,
        "formatter": FormatterConfig,
        "vocabulary": VocabularyConfig,
        "insert": InsertConfig,
        "feedback": FeedbackConfig,
        "logging": LoggingConfig,
        "privacy": PrivacyConfig,
        "cleanup": CleanupConfig,
        "guardrails": GuardrailConfig,
        "llm": LLMConfig,
        "streaming": StreamingConfig,
        "preview": PreviewConfig,
    }
    for name, cls in simple.items():
        raw = data.get(name, {})
        if name == "audio" and isinstance(raw.get("input_device"), int):
            raw = {**raw}
            device = raw.pop("input_device")
            section = _build_section(cls, name, raw)
            section.input_device = device
        else:
            section = _build_section(cls, name, raw)
        setattr(cfg, name, section)

    cmd_raw = dict(data.get("commands", {}))
    phrases = defaults["commands"]["phrases"] | cmd_raw.pop("phrases", {})
    cfg.commands = _build_section(CommandsConfig, "commands", cmd_raw)
    cfg.commands.phrases = phrases

    cfg.replacements = _parse_replacements(data.get("replacements", {}))
    if "models_dir" in data:
        cfg.models_dir = Path(data["models_dir"]).expanduser()
    for key in data:
        if key not in {*simple, "commands", "replacements", "models_dir"}:
            log.warning("config: unknown section [%s] (ignored)", key)
    _validate(cfg)
    return cfg


def ensure_config_file(path: Path | None = None) -> Path:
    path = path or paths.config_path()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(DEFAULT_CONFIG_TOML, encoding="utf-8")
        log.info("created default config at %s", path)
    return path


def _default_sections() -> dict[str, str]:
    """Top-level section name -> its block of the default template (subtables included)."""
    blocks: dict[str, str] = {}
    current: str | None = None
    for line in DEFAULT_CONFIG_TOML.splitlines(keepends=True):
        if line.startswith("[") and not line.startswith("[["):
            name = line.strip().strip("[]").split(".")[0]
            if name != current and name not in blocks:
                current = name
                blocks[name] = ""
        if current is not None:
            blocks[current] += line
    return blocks


def upgrade_config_file(path: Path) -> list[str]:
    """Append sections added in newer versions (with their comments) to an existing config.
    Existing settings are never modified."""
    text = path.read_text(encoding="utf-8")
    try:
        present = set(tomllib.loads(text))
    except tomllib.TOMLDecodeError:
        return []  # leave a broken file alone; parse_config reports the error
    added = [name for name in _default_sections() if name not in present]
    if added:
        blocks = "".join(_default_sections()[n] for n in added)
        sep = "" if text.endswith("\n") else "\n"
        path.write_text(f"{text}{sep}\n# --- added by a dictate update ---\n{blocks}", encoding="utf-8")
        log.info("config: added new settings sections %s to %s", ", ".join(added), path)
    return added


def load_config(path: Path | None = None) -> Config:
    path = ensure_config_file(path)
    upgrade_config_file(path)
    return parse_config(path.read_text(encoding="utf-8"), source_path=path)


class ConfigWatcher:
    """Reloads the config when the file's mtime changes. Call `maybe_reload()` between utterances."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._mtime = self._stat()

    def _stat(self) -> float:
        p = self.cfg.source_path
        try:
            return p.stat().st_mtime if p else 0.0
        except OSError:
            return 0.0

    def maybe_reload(self) -> bool:
        mtime = self._stat()
        if mtime == self._mtime or self.cfg.source_path is None:
            return False
        self._mtime = mtime
        try:
            new = load_config(self.cfg.source_path)
        except ConfigError as e:
            log.error("config reload failed, keeping previous settings: %s", e)
            return False
        # Only the hot-reloadable sections change in place.
        for name in (
            "formatter",
            "commands",
            "vocabulary",
            "replacements",
            "insert",
            "feedback",
            "guardrails",
        ):
            setattr(self.cfg, name, getattr(new, name))
        self.cfg.logging.log_transcripts = new.logging.log_transcripts
        self.cfg.asr.language = new.asr.language
        log.info("config reloaded")
        return True
