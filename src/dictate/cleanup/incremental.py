"""Incremental cleanup: clean finished sentences in the background while the user keeps talking.

Segments arrive from the streaming transcriber at natural pauses. A run of segments is
*locked* (sent to the cleanup model and never revisited) only at a boundary where it's safe:

  - the segment ends a sentence (. ! ?), and
  - the next segment doesn't start with a self-correction ("No wait, Wednesday", "Actually...",
    "Scratch that") or continue the sentence ("and...", "because..."), and
  - no list is in progress ("First... Second..." stays together so it can become a list).

So "Let's meet Tuesday. <pause> No wait, Wednesday." is still cleaned as one piece. At key
release only the unlocked tail is cleaned, so long dictations don't pay for the whole text.
"""

from __future__ import annotations

import logging
import queue
import re
import threading
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

_CORRECTION_START = {
    "en": r"no|nope|wait|no wait|actually|sorry|i mean|scratch that|make that|or rather|rather|instead|correction",
    "es": r"no|perdón|perdon|mejor dicho|quiero decir|digo|borra eso|en realidad",
    "fr": r"non|pardon|plutôt|plutot|je veux dire|enfin|efface ça|efface ca|en fait",
}
# Words that almost never start a spoken sentence. ("And", "But", "So", "For", "If"... often do,
# and a phrase that really continues the sentence usually comes out lowercase, which is checked too.)
_CONTINUATION_START = {
    "en": r"which|who|whom|whose|than|of|to|with|from|by|whereas|plus",
    "es": r"que|cuyo|cuya|de|con|para|sino",
    "fr": r"que|qui|dont|de|avec|pour|par|sinon",
}
_LIST_CUE = {
    "en": r"first|second|third|fourth|fifth|firstly|secondly|thirdly|next|finally|lastly|number one|number two|"
          r"one|two|three|four|five|step one|step two",
    "es": r"primero|segundo|tercero|cuarto|después|finalmente|por último|uno|dos|tres",
    "fr": r"premièrement|deuxièmement|troisièmement|d'abord|ensuite|enfin|finalement|un|deux|trois",
}  # fmt: skip


def _start_rx(alts: str) -> re.Pattern[str]:
    return re.compile(r"^\W*(?:" + alts + r")(?![\w'])", re.IGNORECASE)


@dataclass
class _Rules:
    correction: re.Pattern[str]
    continuation: re.Pattern[str]
    list_cue: re.Pattern[str]
    open_end: re.Pattern[str] | None = None  # a spoken "comma" / "open quote" ends the segment
    mark_start: re.Pattern[str] | None = None  # a spoken "period" / "question mark" starts the next one


def _phrases_rx(language: str, names: set[str], *, at_end: bool) -> re.Pattern[str] | None:
    from dictate.cleanup.commands import _alt
    from dictate.config import DEFAULT_PHRASES

    phrases = [p for n, ps in DEFAULT_PHRASES.get(language, {}).items() if n in names for p in ps]
    if not phrases:
        return None
    alts = "|".join(_alt(p) for p in sorted(phrases, key=len, reverse=True))
    if at_end:
        return re.compile(r"(?<![\w'’])(?:" + alts + r")[\W]*$", re.IGNORECASE)
    return re.compile(r"^\W*(?:" + alts + r")(?![\w'’])", re.IGNORECASE)


_OPEN_MARKS = {"comma", "colon", "semicolon", "open_quote", "open_paren", "open_question", "open_exclamation"}
_CLOSE_MARKS = {"comma", "period", "question_mark", "exclamation_mark", "colon", "semicolon", "ellipsis",
                "close_quote", "close_paren"}  # fmt: skip


def rules_for(language: str) -> _Rules:
    lang = language if language in _CORRECTION_START else "en"
    return _Rules(
        correction=_start_rx(_CORRECTION_START[lang]),
        continuation=_start_rx(_CONTINUATION_START[lang]),
        list_cue=_start_rx(_LIST_CUE[lang]),
        open_end=_phrases_rx(lang, _OPEN_MARKS, at_end=True),
        mark_start=_phrases_rx(lang, _CLOSE_MARKS, at_end=False),
    )


def safe_boundary(left: str, right: str, rules: _Rules) -> bool:
    """May `left` be cleaned without seeing `right`?"""
    left, right = left.strip(), right.strip()
    if not left:
        return True
    if not re.search(r"[.!?]['\")\]]*$", left):
        return False
    if rules.open_end is not None and rules.open_end.search(left):
        return False
    if right and rules.mark_start is not None and rules.mark_start.match(right):
        return False
    if right and (rules.correction.match(right) or rules.continuation.match(right)):
        return False
    if right and right[0].islower():
        return False
    # a list item on either side: keep the list together so it can be formatted as one
    last_sentence = re.split(r"(?<=[.!?])\s+", left)[-1]
    return not (rules.list_cue.match(last_sentence) or rules.list_cue.match(right))


_LIST_LINE = re.compile(r"(?:^|\n)[ \t]*(?:\d+[.)]|[-*•])[ \t]+[^\n]*$")


def join_chunks(parts: list[str]) -> str:
    out = ""
    for p in parts:
        if not p:
            continue
        if not out or out.endswith("\n") or p.startswith("\n"):
            out += p
        elif _LIST_LINE.search(out):  # don't glue the next sentence onto the last list item
            out += "\n\n" + p
        else:
            out += " " + p
    return out


class IncrementalCleaner:
    """Owns its own worker thread; `clean_chunk(text)` must return an object with `.text`."""

    def __init__(self, clean_chunk: Callable[[str], Any], language: str = "en", *, min_words: int = 8) -> None:
        self.clean_chunk = clean_chunk
        self.rules = rules_for(language)
        self.min_words = min_words
        self._pending: list[str] = []
        self._locked: list[Future] = []
        self._lock = threading.Lock()
        self._q: queue.Queue[tuple[str, Future] | None] = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="incremental-cleanup", daemon=True)
        self._thread.start()
        self.locked_segments = 0

    def _run(self) -> None:
        while (item := self._q.get()) is not None:
            text, fut = item
            try:
                fut.set_result(self.clean_chunk(text))
            except Exception as e:
                log.exception("incremental cleanup failed")
                fut.set_exception(e)

    def commit(self, text: str) -> None:
        """A finished segment's ASR text (called in segment order)."""
        with self._lock:
            self._pending.append(text)
            self._try_lock()

    def _try_lock(self) -> None:
        p = self._pending
        k = 0
        for i in range(len(p) - 1, 0, -1):  # latest safe boundary wins: lock p[:i]
            if safe_boundary(p[i - 1], p[i], self.rules):
                k = i
                break
        if k == 0:
            return
        chunk = " ".join(s for s in p[:k] if s.strip())
        if len(chunk.split()) < self.min_words:
            return
        fut: Future = Future()
        self._locked.append(fut)
        self._q.put((chunk, fut))
        self.locked_segments += k
        del p[:k]

    def finalize(self, final_text: str, timeout_s: float = 10.0) -> tuple[list[Any], str]:
        """Returns (results of the locked chunks, in order) and the raw tail still to clean."""
        with self._lock:
            self._pending.append(final_text)
            tail = " ".join(s for s in self._pending if s.strip())
            self._pending = []
            locked = list(self._locked)
        results = [f.result(timeout=timeout_s) for f in locked]
        self._q.put(None)
        return results, tail

    def abandon(self) -> None:
        self._q.put(None)
