"""Snap near-miss transcriptions onto the user's vocabulary (deterministic, before the LLM).

Parakeet can't be biased with a hotword list, so we correct afterwards:
  "Professor Okonko"     -> "Professor Okonkwo"     (close spelling, vocabulary has "Okonkwo")
  "store it in PG Vector" -> "store it in pgvector"  (pieces that join into a vocabulary term)

Deliberately strict: a single word is only changed when it's >= 5 letters and very similar
(rapidfuzz ratio >= threshold) to a vocabulary term but not the same word. Same-word casing
changes ("mark" vs "Mark") are left to the LLM, which sees the vocabulary in its prompt and
can tell a name from a verb. Words inside URLs, emails and paths are never touched.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from itertools import pairwise

from rapidfuzz import fuzz

_TOKEN = re.compile(r"(?<![\w@./\\-])[^\W\d_][\w'’]*(?![\w@\\-])(?![./][\w/])", re.UNICODE)


def _norm(s: str) -> str:
    return re.sub(r"[^0-9a-z]", "", s.lower())


@dataclass(frozen=True)
class _Term:
    text: str
    norm: str
    n_words: int


@lru_cache(maxsize=32)
def _terms(vocab: tuple[str, ...]) -> tuple[_Term, ...]:
    out = []
    for v in vocab:
        v = v.strip()
        n = _norm(v)
        if len(n) >= 4:
            out.append(_Term(v, n, len(v.split())))
    return tuple(out)


def apply_vocabulary(text: str, vocabulary: list[str] | tuple[str, ...], *, threshold: int = 86) -> str:
    terms = _terms(tuple(vocabulary))
    if not terms or not text:
        return text
    tokens = [(m.start(), m.end(), m.group(0)) for m in _TOKEN.finditer(text)]
    candidates: list[tuple[float, int, int, str]] = []  # (score, first token, last token, replacement)
    for t in terms:
        sizes = sorted({max(1, t.n_words - 1), t.n_words, t.n_words + 1, t.n_words + 2})
        for size in sizes:
            for i in range(len(tokens) - size + 1):
                window = tokens[i : i + size]
                # words in the window must be adjacent (only spaces between them)
                if any(text[a[1] : b[0]].strip() for a, b in pairwise(window)):
                    continue
                surface = text[window[0][0] : window[-1][1]]
                wn = _norm(surface)
                if not wn or surface == t.text:
                    continue
                if wn == t.norm:
                    if size == 1:
                        continue  # same word, only casing differs: leave it to the LLM
                    score = 100.0  # "PG Vector" -> "pgvector"
                elif size == 1 and t.n_words == 1 and len(t.norm) >= 5:
                    score = fuzz.ratio(wn, t.norm)
                    if score < threshold or abs(len(wn) - len(t.norm)) > 2:
                        continue
                elif size > 1:
                    score = fuzz.ratio(wn, t.norm)
                    if score < max(threshold, 90):
                        continue
                else:
                    continue
                candidates.append((score, i, i + size - 1, t.text))

    # best matches first, no overlaps
    chosen: list[tuple[int, int, str]] = []
    taken: set[int] = set()
    for _score, a, b, repl in sorted(candidates, key=lambda c: (-c[0], c[1])):
        if any(k in taken for k in range(a, b + 1)):
            continue
        taken.update(range(a, b + 1))
        chosen.append((a, b, repl))
    for a, b, repl in sorted(chosen, reverse=True):
        start, end = tokens[a][0], tokens[b][1]
        text = text[:start] + repl + text[end:]
    return text
