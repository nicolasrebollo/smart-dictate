"""Typing as you speak: which sentences are typed when, with the right spaces, and nothing typed twice
or before a correction can still change it."""

import time

from dictate.config import parse_config
from dictate.engine import EngineSession
from dictate.pipeline import Pipeline, PipelineResult


class _ASR:
    def transcribe(self, audio, **kw):
        raise AssertionError("not used")


def _live(segments: list[str], final: str) -> tuple[list[str], str, str]:
    """Commit `segments` as the streaming transcriber would, then finish with the open `final` one.
    Returns (pieces typed while speaking, what's left at the end, the whole text)."""
    typed: list[str] = []
    pipe = Pipeline(parse_config(""), _ASR(), None)
    inc = pipe.new_incremental(lambda res: typed.append(res.text))
    assert inc is not None  # live typing works without the AI cleanup too
    for seg in segments:
        inc.commit(seg)
    time.sleep(0.2)
    while_speaking = list(typed)
    res, rest = pipe.finish_live([*segments, final], incremental=inc)
    return while_speaking, rest, res.text


def test_sentences_are_typed_once_the_next_one_starts() -> None:
    during, rest, whole = _live(["let's meet on Tuesday.", "The room is booked."], "Bring snacks.")
    assert during == ["Let's meet on Tuesday."]  # the second waits: the next phrase might correct it
    assert rest == " The room is booked. Bring snacks."
    assert whole == during[0] + rest


def test_a_correction_is_applied_before_anything_is_typed() -> None:
    during, rest, whole = _live(["Let's meet Tuesday."], "No wait, Wednesday.")
    assert during == []  # "Tuesday." was held back: it could still be corrected
    assert whole == rest


def test_nothing_finished_yet_means_everything_comes_at_the_end() -> None:
    during, rest, whole = _live(["so the plan is"], "to ship on friday.")
    assert during == [] and rest == whole and whole.endswith(".")


def test_the_engine_hands_on_each_piece_with_its_space() -> None:
    got: list[str] = []
    s = object.__new__(EngineSession)
    s._done, s._typed, s._on_text = False, [], got.append
    for text in ["One.", "Two.", "", "Three."]:
        s._chunk_ready(PipelineResult(text, "", 0.0, 0.0))
    assert got == ["One.", " Two.", " Three."]
    s._done = True  # cancelled: nothing more is typed
    s._chunk_ready(PipelineResult("Four.", "", 0.0, 0.0))
    assert got == ["One.", " Two.", " Three."]
