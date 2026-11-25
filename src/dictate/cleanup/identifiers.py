"""Spoken identifiers -> code identifiers, deterministically (before the LLM, and in raw mode).

  "snake case user id"            -> user_id
  "camel case get user name"      -> getUserName
  "pascal case user profile"      -> UserProfile
  "kebab case main menu"          -> main-menu
  "constant case max retries"     -> MAX_RETRIES   (also "screaming snake case")
  "max underscore retries"        -> max_retries
  "dash dash verbose"             -> --verbose

After a "<style> case" cue, up to 5 following words are joined; joining stops early at a
function word ("to", "and", "in", "inside", "for"...) or at punctuation.
"""

from __future__ import annotations

import re

_STYLES = r"(screaming[ \t]+snake|snake|camel|pascal|kebab|constant|upper[ \t]+snake)"
_CUE = re.compile(r"(?<![\w])" + _STYLES + r"[ \t]+case[ \t]+", re.IGNORECASE)
_TOKEN = re.compile(r"([A-Za-z][A-Za-z0-9']*)([^\w\s]*)([ \t]*)")
_STOP = {
    "to", "and", "or", "in", "inside", "into", "for", "from", "with", "the", "a", "an", "of", "on", "at",
    "as", "is", "are", "was", "be", "then", "by", "it", "this", "that", "so", "but", "if", "when",
    "variable", "function", "method", "class", "field", "property", "parameter", "argument", "file",
}  # fmt: skip
_MAX_WORDS = 5


def _join(style: str, parts: list[str]) -> str:
    style = re.sub(r"\s+", " ", style.lower())
    low = [p.lower() for p in parts]
    if style == "snake":
        return "_".join(low)
    if style in ("constant", "screaming snake", "upper snake"):
        return "_".join(p.upper() for p in parts)
    if style == "kebab":
        return "-".join(low)
    if style == "camel":
        return low[0] + "".join(p.capitalize() for p in low[1:])
    return "".join(p.capitalize() for p in low)  # pascal


def _apply_case_cues(text: str) -> str:
    out: list[str] = []
    pos = 0
    while m := _CUE.search(text, pos):
        out.append(text[pos : m.start()])
        style = m.group(1)
        i = m.end()
        parts: list[str] = []
        trailing = ""
        while len(parts) < _MAX_WORDS and (t := _TOKEN.match(text, i)):
            word, punct, space = t.groups()
            if parts and word.lower() in _STOP:
                break
            parts.append(word)
            i = t.end()
            if punct:
                trailing = punct + space
                break
            trailing = space
        if not parts:  # "snake case" with nothing after it: leave as said
            out.append(m.group(0))
            pos = m.end()
            continue
        out.append(_join(style, parts) + trailing)
        pos = i
    out.append(text[pos:])
    return "".join(out)


_UNDERSCORE = re.compile(r"\b([A-Za-z0-9]+)[ \t]+underscore[ \t]+(?=[A-Za-z0-9])", re.IGNORECASE)
_DASH_DASH = re.compile(r"\bdash[ \t]+dash[ \t]+(?=[A-Za-z])", re.IGNORECASE)


def apply_identifiers(text: str) -> str:
    text = _apply_case_cues(text)
    while True:  # "a underscore b underscore c" -> a_b_c
        new = _UNDERSCORE.sub(r"\1_", text)
        if new == text:
            break
        text = new
    return _DASH_DASH.sub("--", text)
