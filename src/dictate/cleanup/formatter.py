"""Deterministic formatter (no LLM).

Used for raw mode, as the guardrail fallback, and for utterances that skip the LLM.
Split in two so the LLM path can reuse it:
  pre_format:  voice commands, filler removal, repeated-word collapse
  post_format: spacing/punctuation tidy, capitalization, final period, known-term casing
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from dictate.cleanup.commands import apply_commands
from dictate.cleanup.identifiers import apply_identifiers
from dictate.cleanup.terms import apply_terms
from dictate.cleanup.vocab import apply_vocabulary
from dictate.config import Config

# Conservative on purpose: only sounds that are never real words. "like", "you know", "este",
# "bueno", "genre", "ben"... are left to the LLM, which can tell filler from meaning.
FILLERS: dict[str, list[str]] = {
    "en": ["um+", "uh+", "uhm+", "erm+", "er", "hmm+", "mm+"],
    "es": ["eh+m*", "em+", "mm+"],
    "fr": ["euh+", "heu+", "hum+", "mm+"],
}

# Words where an immediate repeat is usually intentional ("I know that that's true",
# "nous nous sommes", "no, no", "very very").
REPEAT_OK: dict[str, set[str]] = {
    "en": {"that", "had", "is", "very", "really", "so", "no", "yes", "bye", "ha", "go", "knock", "do", "la"},
    "es": {"no", "sí", "si", "muy", "ya", "ja", "que", "chao", "adiós"},
    "fr": {"nous", "vous", "non", "oui", "très", "si", "ha", "bon"},
}

ABBREVIATIONS = {
    "en": {"e.g.", "i.e.", "etc.", "vs.", "mr.", "mrs.", "ms.", "dr.", "prof.", "st.", "approx.", "no."},
    "es": {"p.ej.", "etc.", "sr.", "sra.", "dr.", "dra.", "ud.", "uds."},
    "fr": {"p.ex.", "etc.", "m.", "mme.", "dr.", "cf."},
}


@dataclass
class FormatOptions:
    language: str = "en"
    remove_fillers: bool = True
    collapse_repeats: bool = True
    final_period: bool = True
    commands_enabled: bool = True
    phrases: dict[str, list[str]] = field(default_factory=dict)
    builtin_terms: bool = True
    spoken_identifiers: bool = True
    vocabulary: list[str] = field(default_factory=list)

    @classmethod
    def from_config(cls, cfg: Config) -> FormatOptions:
        lang = cfg.asr.language
        return cls(
            language=lang,
            remove_fillers=cfg.formatter.remove_fillers,
            collapse_repeats=cfg.formatter.collapse_repeats,
            final_period=cfg.formatter.final_period,
            commands_enabled=cfg.commands.enabled,
            phrases=cfg.commands.phrases.get(lang, {}),
            builtin_terms=cfg.cleanup.builtin_terms,
            spoken_identifiers=cfg.formatter.spoken_identifiers,
            vocabulary=cfg.vocabulary.words,
        )


@dataclass
class FormatResult:
    text: str
    scratch_previous: bool = False


# Words after which a comma is natural even once the filler is gone ("So, um, I think" -> "So, I think").
_DISCOURSE = {
    "so", "well", "okay", "ok", "yeah", "yes", "no", "right", "now", "anyway", "actually", "honestly", "basically",
    "bueno", "pues", "vale", "sí", "si", "bon", "alors", "oui", "non", "donc",
}  # fmt: skip


def remove_fillers(text: str, language: str) -> str:
    words = "|".join(FILLERS.get(language, FILLERS["en"]))
    # "Should we, uh, hire" -> "Should we hire": a filler set off by commas takes both commas with it
    between = re.compile(r"(\w+)[ \t]*,[ \t]*(?:" + words + r")[ \t]*,[ \t]*", re.IGNORECASE)
    text = between.sub(lambda m: m.group(1) + ("," if m.group(1).lower() in _DISCOURSE else "") + " ", text)
    rx = re.compile(r"(?<![\w'’-])(?:" + words + r")(?![\w'’-])[ \t]*(?:[,…]+|\.{3})?[ \t]*", re.IGNORECASE)
    return rx.sub(" ", text)


_STUTTER = re.compile(r"(?<!\w)(\w{1,4})-[ \t]+(?=\1)", re.IGNORECASE)


def collapse_repeats(text: str, language: str) -> str:
    text = _STUTTER.sub("", text)  # "th- the" -> "the"
    ok = REPEAT_OK.get(language, set())
    rx = re.compile(r"(?<![\w'’])(\w+)((?:[ \t]*,?[ \t]+\1(?![\w'’]))+)", re.IGNORECASE)

    def repl(m: re.Match[str]) -> str:
        word = m.group(1)
        if word.lower() in ok or word.isdigit():
            return m.group(0)
        return word

    return rx.sub(repl, text)


def tidy_spacing(text: str, language: str) -> str:
    lines = []
    for line in text.split("\n"):
        line = re.sub(r"[ \t]+", " ", line).strip()
        # orphan punctuation left behind by removals: ", ," / ", ." / leading ", "
        line = re.sub(r",(?:\s*,)+", ",", line)
        line = re.sub(r",\s*([.!?;:])", r"\1", line)
        line = re.sub(r"^[,;:]\s*", "", line)
        line = re.sub(r"([.!?])\s*,", r"\1", line)
        line = re.sub(r" ([,.])(?!\d)", r"\1", line)
        if language != "fr":  # French typography puts a space before ; : ! ?
            line = re.sub(r" ([;:!?])", r"\1", line)
        lines.append(line)
    return "\n".join(lines).strip("\n ")


_SENTENCE_START = re.compile(r"(^|[.!?…]\s+|\n\s*)([¿¡\"'“‘«(\[]*\s*)([^\W\d_])", re.UNICODE)


def capitalize(text: str, language: str) -> str:
    abbrevs = ABBREVIATIONS.get(language, set())

    def repl(m: re.Match[str]) -> str:
        boundary, opener, letter = m.group(1), m.group(2), m.group(3)
        if boundary.strip().endswith("."):
            prev_word = text[: m.start(1) + len(boundary.rstrip())].split()[-1:] or [""]
            if prev_word[0].lower() in abbrevs:
                return m.group(0)
        return boundary + opener + letter.upper()

    text = _SENTENCE_START.sub(repl, text)
    if language == "en":
        text = re.sub(r"(?<![\w'’.])i(?!\.\w\.)(?=(?:['’](?:m|ve|ll|d))?(?![\w'’]))", "I", text)
    return text


_TERMINAL = re.compile(r"[.!?…:;]$|[.!?…][\"'”’»)\]]$")


def add_final_period(text: str) -> str:
    body = text.rstrip("\n ")
    trailing = text[len(body) :]
    if body and not _TERMINAL.search(body) and re.search(r"\w", body):
        body += "."
    return body + trailing


def pre_format(text: str, opts: FormatOptions) -> FormatResult:
    scratch_previous = False
    if opts.commands_enabled and opts.phrases:
        res = apply_commands(text, opts.phrases)
        text, scratch_previous = res.text, res.scratch_previous
    if opts.spoken_identifiers:
        text = apply_identifiers(text)
    if opts.vocabulary:
        text = apply_vocabulary(text, opts.vocabulary)
    if opts.remove_fillers:
        text = remove_fillers(text, opts.language)
    if opts.collapse_repeats:
        text = collapse_repeats(text, opts.language)
    return FormatResult(text=tidy_spacing(text, opts.language), scratch_previous=scratch_previous)


def post_format(text: str, opts: FormatOptions) -> str:
    text = tidy_spacing(text, opts.language)
    if not text:
        return ""
    text = capitalize(text, opts.language)
    if opts.final_period:
        text = add_final_period(text)
    return _finish(text, opts)


def post_format_llm(text: str, opts: FormatOptions) -> str:
    """After a successful LLM pass: the model already handled casing and punctuation, so only
    tidy whitespace and apply known-term casing."""
    return _finish(_capitalize_after_newlines(tidy_spacing(text, opts.language)), opts)


def _capitalize_after_newlines(text: str) -> str:
    """'Hi team,\\nquick update' -> 'Hi team,\\nQuick update'. Words with capitals, digits or
    symbols elsewhere (iPhone, npm-style identifiers) are left alone."""

    def repl(m: re.Match[str]) -> str:
        word = m.group(2)
        if not word.isalpha() or not word.islower():
            return m.group(0)
        return m.group(1) + word[0].upper() + word[1:]

    return re.sub(r"(\n[ \t]*)([^\W\d_][\w'’-]*)", repl, text)


_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+(?![\w-])")


def _finish(text: str, opts: FormatOptions) -> str:
    text = _EMAIL.sub(lambda m: m.group(0).lower(), text)  # "John.Smith@Example.com" -> lowercase
    if opts.builtin_terms:
        text = apply_terms(text)
    return text


def format_raw(text: str, opts: FormatOptions) -> FormatResult:
    pre = pre_format(text, opts)
    return FormatResult(text=post_format(pre.text, opts), scratch_previous=pre.scratch_previous)
