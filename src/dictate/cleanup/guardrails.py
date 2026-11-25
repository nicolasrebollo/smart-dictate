"""Validate the LLM's output. Any failure -> fall back to the rule-formatted transcript.

Checks (each returns a short reason string):
  empty / tags / assistant-speak opener that the speaker didn't say
  word-count ratio outside [min_ratio, max_ratio]
  too many invented words (not said, not in vocabulary, not a known term, not a respelling)
  a question that stopped being a question
  lost line breaks
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from itertools import pairwise

from rapidfuzz import fuzz

from dictate.config import GuardrailConfig

_ASSISTANT_OPENERS = re.compile(
    r"^\s*(sure|certainly|of course|absolutely|here'?s|here is|here are|i can'?t|i cannot|i'm sorry|"
    r"i am sorry|as an ai|i'd be happy|i would be happy|okay, here|ok, here|great question|"
    r"claro|aquí tienes|por supuesto|bien sûr|voici|désolé)\b",
    re.IGNORECASE,
)
_TAGS = re.compile(r"</?\s*(clean|transcript|context)\s*>|<\|", re.IGNORECASE)

# Words the cleanup may legitimately introduce when formatting what was said.
_FORMAT_WORDS = {
    "am",
    "pm",
    "a.m",
    "p.m",
    "st",
    "nd",
    "rd",
    "th",
    "kg",
    "km",
    "cm",
    "mm",
    "ms",
    "mb",
    "gb",
    "tb",
    "kb",
    "usd",
    "eur",
    "cad",
    "h",
    "min",
    "sec",
    "vs",
    "etc",
    "e.g",
    "i.e",
    "dr",
    "mr",
    "mrs",
    "jr",
    "sr",
    "ave",
    "no",
    "pi",
    "sqrt",
    "and",
    "or",
    "the",
    "to",
    "of",
    "a",
    "an",
    "is",
}

# A transcript that starts with one of these is a dictated request. The model sometimes "does"
# it (e.g. drops "Make this sound more professional:" and rewrites the rest), which must fail.
_REQUEST_VERBS = {
    "make", "write", "rewrite", "rephrase", "translate", "summarize", "summarise", "fix", "tell", "give",
    "list", "reply", "respond", "answer", "explain", "convert", "draft", "create", "generate", "shorten",
    "expand", "edit", "correct", "proofread", "improve", "format", "describe", "compose", "send", "help",
    "escribe", "traduce", "resume", "corrige", "explica", "dame", "dime", "haz", "responde", "redacta",
    "ecris", "traduis", "explique", "donne", "dis", "fais", "reponds", "redige",
}  # fmt: skip
_LEAD_SKIP = {"um", "uh", "so", "okay", "ok", "please", "hey", "and", "now", "just", "eh", "euh", "bueno", "can", "you"}

_WORD = re.compile(r"[^\W\d_][\w'’-]*", re.UNICODE)


@dataclass
class GuardResult:
    ok: bool
    reason: str = ""


def _fold(word: str) -> str:
    w = unicodedata.normalize("NFKD", word.lower())
    return "".join(c for c in w if not unicodedata.combining(c)).replace("’", "'").strip("'-")


def words(text: str) -> list[str]:
    return [_fold(w) for w in _WORD.findall(text)]


def _counted_words(text: str) -> int:
    """Words for the ratio check: alphabetic tokens + numbers (lists/symbols don't count)."""
    return len(re.findall(r"[^\W_][\w'’.-]*", text, re.UNICODE))


def novel_words(raw: str, cleaned: str, allowed: Iterable[str] = ()) -> list[str]:
    raw_words = words(raw)
    raw_set = set(raw_words)
    joined = {a + b for a, b in pairwise(raw_words)}
    joined |= {a + b + c for a, b, c in zip(raw_words, raw_words[1:], raw_words[2:], strict=False)}
    allowed_set = {_fold(a) for a in allowed} | _FORMAT_WORDS
    out = []
    for w in words(cleaned):
        if w in raw_set or w in allowed_set or w in joined or len(w) <= 1:
            continue
        # respelling of something that was said ("Okonko" -> "Okonkwo", "post gress" -> "postgres")
        if any(fuzz.ratio(w, r) >= 75 for r in raw_set if abs(len(r) - len(w)) <= 3):
            continue
        if any(fuzz.ratio(w, j) >= 80 for j in joined if abs(len(j) - len(w)) <= 3):
            continue
        out.append(w)
    return out


def check(
    raw: str,
    cleaned: str,
    cfg: GuardrailConfig,
    *,
    allowed: Iterable[str] = (),
) -> GuardResult:
    if not cleaned.strip():
        return GuardResult(False, "empty output")
    if _TAGS.search(cleaned):
        return GuardResult(False, "output contains prompt tags")
    opener = _ASSISTANT_OPENERS.match(cleaned)
    if opener and not _ASSISTANT_OPENERS.match(raw):
        return GuardResult(False, f"assistant-style opener {opener.group(1)!r}")

    n_raw, n_clean = _counted_words(raw), _counted_words(cleaned)
    if n_raw >= 6:
        ratio = n_clean / n_raw
        if not cfg.min_ratio <= ratio <= cfg.max_ratio:
            return GuardResult(False, f"length ratio {ratio:.2f} outside [{cfg.min_ratio}, {cfg.max_ratio}]")
    elif n_clean > n_raw + 4:
        return GuardResult(False, f"short input grew from {n_raw} to {n_clean} words")

    novel = novel_words(raw, cleaned, allowed)
    out_words = max(1, len(words(cleaned)))
    if len(novel) >= cfg.min_novel_words and len(novel) / out_words > cfg.max_novel_fraction:
        return GuardResult(False, f"{len(novel)} invented words ({', '.join(novel[:5])})")

    lead = [w for w in words(raw)[:5] if w not in _LEAD_SKIP][:2]
    request = next((w for w in lead if w in _REQUEST_VERBS), None)
    if request and request not in words(cleaned):
        return GuardResult(False, f"dropped the dictated request {request!r}")

    if "?" in raw and "?" not in cleaned and "¿" not in cleaned:
        return GuardResult(False, "question turned into a statement")

    if cleaned.count("\n") < raw.count("\n"):
        return GuardResult(False, "line breaks lost")
    return GuardResult(True)
