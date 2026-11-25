"""Deterministic voice commands: "new line", "new paragraph", "scratch that" (+ es/fr).

A phrase only counts as a command when it forms a whole clause: preceded by the start of
the text or by punctuation/newline, and followed by punctuation or the end. So
"Hi Sam. New paragraph. Thanks" triggers, but "I wrote a new paragraph today" does not.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_BOUNDARY_BEFORE = r"(?:^|(?<=[.!?,;:\n]))"
_BOUNDARY_AFTER = r"[ \t]*(?:[.!?,;:]+|$)"


@dataclass
class CommandResult:
    text: str
    scratch_previous: bool = False  # "scratch that" with nothing before it in this utterance


def _phrase_regex(phrases: list[str]) -> re.Pattern[str] | None:
    alts = [r"[ \t]+".join(re.escape(w) for w in p.split()) for p in phrases if p.strip()]
    if not alts:
        return None
    return re.compile(
        _BOUNDARY_BEFORE + r"[ \t]*(?:" + "|".join(sorted(alts, key=len, reverse=True)) + r")" + _BOUNDARY_AFTER,
        re.IGNORECASE | re.MULTILINE,
    )


_SENTENCE_END = re.compile(r"[.!?\n]")


def _drop_last_sentence(prefix: str) -> str:
    """Remove the last sentence (or clause run) from `prefix`."""
    body = prefix.rstrip(" \t,;:")
    core = body.rstrip(".!?")  # the sentence being scratched may end with punctuation
    ends = [m.end() for m in _SENTENCE_END.finditer(core)]
    if not ends:
        return ""
    return core[: ends[-1]]


def apply_commands(text: str, phrases: dict[str, list[str]]) -> CommandResult:
    scratch_previous = False

    scratch = _phrase_regex(phrases.get("scratch_that", []))
    if scratch is not None:
        while m := scratch.search(text):
            prefix = text[: m.start()]
            if not prefix.strip():
                scratch_previous = True
                text = text[m.end() :]
            else:
                kept = _drop_last_sentence(prefix)
                text = kept + " " + text[m.end() :].lstrip()

    for name, marker in (("new_paragraph", "\n\n"), ("new_line", "\n")):
        rx = _phrase_regex(phrases.get(name, []))
        if rx is not None:
            text = rx.sub(marker, text)

    # tidy whitespace around inserted line breaks
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return CommandResult(text=text.strip(" \t"), scratch_previous=scratch_previous)
