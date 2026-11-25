"""User replacement table, applied last: whole-word, case-insensitive unless asked otherwise."""

from __future__ import annotations

import re
from functools import lru_cache

from dictate.config import Replacement


@lru_cache(maxsize=512)
def _compile(source: str, case_sensitive: bool) -> re.Pattern[str]:
    words = r"\s+".join(re.escape(w) for w in source.split())
    return re.compile(r"(?<![\w'’])" + words + r"(?![\w'’])", 0 if case_sensitive else re.IGNORECASE)


def apply_replacements(text: str, replacements: list[Replacement]) -> str:
    # Longest source first so "new york times" wins over "new york".
    for r in sorted(replacements, key=lambda r: len(r.source), reverse=True):
        if not r.source.strip():
            continue
        text = _compile(r.source, r.case_sensitive).sub(lambda _m, to=r.to: to, text)
    return text
