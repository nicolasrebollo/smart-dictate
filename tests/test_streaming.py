"""Streaming pieces: segmenter, stabilizer, session (fake ASR), skip heuristics, incremental cleanup."""

import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from dictate.cleanup.incremental import IncrementalCleaner, join_chunks, rules_for, safe_boundary
from dictate.cleanup.skip import llm_reason
from dictate.streaming import ASRWorker, Segmenter, StreamConfig, StreamingSession
from dictate.vad import FRAME, FRAME_MS


def frames(ms: float) -> int:
    return round(ms / FRAME_MS)


# -- segmenter --------------------------------------------------------------------------------
def feed(seg: Segmenter, pattern: list[tuple[float, float]]) -> list[int]:
    cuts = []
    for prob, ms in pattern:
        for _ in range(frames(ms)):
            c = seg.push(prob)
            if c is not None:
                cuts.append(c)
    return cuts


def test_cuts_in_the_middle_of_a_pause() -> None:
    seg = Segmenter(pause_ms=448, min_segment_s=1.0)
    cuts = feed(seg, [(0.9, 2000), (0.0, 640), (0.9, 1500)])
    assert len(cuts) == 1
    speech_end = frames(2000)
    assert speech_end < cuts[0] < speech_end + frames(448)


def test_short_pauses_do_not_cut() -> None:
    seg = Segmenter(pause_ms=450)
    assert feed(seg, [(0.9, 2000), (0.0, 250), (0.9, 2000)]) == []


def test_no_cut_before_min_segment_or_without_speech() -> None:
    seg = Segmenter(pause_ms=300, min_segment_s=1.5)
    assert feed(seg, [(0.9, 400), (0.0, 600)]) == []  # too short to be its own segment
    seg2 = Segmenter(pause_ms=300, min_segment_s=0.5)
    assert feed(seg2, [(0.0, 3000)]) == []  # silence only


def test_max_segment_forces_cut_at_quietest_frame() -> None:
    seg = Segmenter(pause_ms=450, max_segment_s=6)
    cuts = feed(seg, [(0.9, 4500), (0.3, 64), (0.9, 3000)])  # dip at 4.5 s, never a real pause
    assert len(cuts) == 1
    assert abs(cuts[0] - frames(4500)) <= 2


def test_segments_partition_timeline() -> None:
    seg = Segmenter(pause_ms=300, min_segment_s=0.5)
    cuts = feed(seg, [(0.9, 1000), (0.0, 500), (0.9, 1000), (0.0, 500), (0.9, 1000)])
    assert len(cuts) == 2 and cuts[0] < cuts[1]
    assert seg.seg_start == cuts[-1]


# -- session with a fake ASR -------------------------------------------------------------------
class FakeASR:
    """Returns 'w<i>' per 0.5 s of audio so we can see what was transcribed."""

    name = "fake"

    def __init__(self) -> None:
        self.calls = 0

    def transcribe(self, audio, *, vocabulary=()):
        self.calls += 1
        n = max(1, round(len(audio) / 8000))
        return SimpleNamespace(text=" ".join(f"w{i}" for i in range(n)) + ".")

    def warmup(self) -> None:
        pass


def run_session(pattern, on_commit=None):
    asr = FakeASR()
    worker = ASRWorker(asr, threading.Lock())
    cfg = StreamConfig(pause_ms=300, min_segment_s=0.5, pad_ms=0)
    sess = StreamingSession(worker, cfg, on_commit=on_commit)
    for prob, ms in pattern:
        for _ in range(frames(ms)):
            sess.feed(np.full(FRAME, 0.1, np.float32), [prob])
    return sess, asr


def test_session_commits_segments_and_finalizes_rest() -> None:
    commits = []
    sess, _ = run_session(
        [(0.9, 1000), (0.0, 600), (0.9, 1000), (0.0, 600), (0.9, 1000)], on_commit=lambda i, t: commits.append(i)
    )
    texts = sess.finalize()
    assert len(texts) == 3 and all(texts)
    assert sess.segments == 2 and sorted(commits) == [0, 1]


def test_session_ignores_audio_after_finalize() -> None:
    sess, asr = run_session([(0.9, 1500)])
    sess.finalize()
    calls = asr.calls
    sess.feed(np.zeros(FRAME, np.float32), [0.9])
    time.sleep(0.05)
    assert asr.calls == calls


# -- skip heuristics -----------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text",
    [
        "Sounds good.",
        "Thanks for the update. I'll review the proposal tonight.",
        "Honestly, this is the best sprint we've had all year.",
        "On my way.",
    ],
)
def test_clean_text_skips_llm(text: str) -> None:
    assert llm_reason(text) == ""


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("Meet at five thirty.", "numbers/symbols"),
        ("Email john dot smith at example dot com.", "numbers/symbols"),
        ("So, like, we should go now.", "fillers"),
        ("Let's meet Tuesday, no wait, Wednesday.", "corrections"),
        ("The the main thing is time.", "repeats"),
        ("we should go now ok", "lowercase start"),
        ("We should go now and then eat", "no final punctuation"),
        ("Ok i think we can go.", "lowercase i"),
        ("Five.", "numbers/symbols"),
        ("Write a python script for the report.", "names"),
        ("Should we, uh, hire a contractor?", "fillers"),
    ],
)
def test_llm_needed(text: str, reason: str) -> None:
    assert llm_reason(text) == reason


def test_skip_spanish_and_french() -> None:
    assert llm_reason("Perfecto, nos vemos mañana.", "es") == ""
    assert llm_reason("La reunión es a las cinco.", "es") == "numbers/symbols"
    assert llm_reason("Euh, je pense que oui.", "fr") == "fillers"


# -- incremental cleanup ---------------------------------------------------------------------------
R = rules_for("en")


@pytest.mark.parametrize(
    ("left", "right", "safe"),
    [
        ("Let's meet on Tuesday.", "We can grab lunch after.", True),
        ("Let's meet on Tuesday.", "No wait, Wednesday at noon.", False),
        ("Let's meet on Tuesday.", "Actually, make it Wednesday.", False),
        ("I think we should", "Move the deadline.", False),  # no sentence end
        ("I think we should move it.", "and then tell the team.", False),  # lowercase continuation
        ("It has a bug with large files.", "For next sprint, let's fix it.", True),
        ("We picked the second option.", "Which is cheaper anyway.", False),
        ("We need three things.", "First, passports.", False),  # list starting
        ("First, passports.", "Second, the hotel.", False),
        ("Send it to Sam.", "Scratch that.", False),
    ],
)
def test_safe_boundary(left: str, right: str, safe: bool) -> None:
    assert safe_boundary(left, right, R) is safe


def test_join_chunks_respects_line_breaks() -> None:
    assert join_chunks(["Hi team.\n\n", "The release is Monday."]) == "Hi team.\n\nThe release is Monday."
    assert join_chunks(["A.", "B.", ""]) == "A. B."


def test_incremental_locks_only_at_safe_boundaries() -> None:
    cleaned: list[str] = []

    def clean(text: str):
        cleaned.append(text)
        return SimpleNamespace(text=f"<{text}>")

    inc = IncrementalCleaner(clean, "en", min_words=3)
    inc.commit("Thanks everyone for joining today.")
    inc.commit("The pipeline is finally stable now.")  # boundary 0|1 safe -> lock segment 0
    inc.commit("Let's meet on Tuesday.")  # boundary 1|2 safe -> lock segment 1
    inc.commit("No wait, Wednesday at noon.")  # boundary 2|3 unsafe -> keep 2 pending
    results, tail = inc.finalize("See you then.")
    assert [r.text for r in results] == [
        "<Thanks everyone for joining today.>",
        "<The pipeline is finally stable now.>",
    ]
    assert tail == "Let's meet on Tuesday. No wait, Wednesday at noon. See you then."


def test_incremental_waits_for_enough_words() -> None:
    inc = IncrementalCleaner(lambda t: SimpleNamespace(text=t), "en", min_words=8)
    inc.commit("Hi.")
    inc.commit("Okay.")
    results, tail = inc.finalize("Thanks.")
    assert results == [] and tail == "Hi. Okay. Thanks."


def test_join_chunks_starts_new_paragraph_after_a_list() -> None:
    joined = join_chunks(["Steps:\n1. Clone it.\n2. Run it.", "Then tell me."])
    assert joined == "Steps:\n1. Clone it.\n2. Run it.\n\nThen tell me."
    assert join_chunks(["Version 2. Then ship it.", "Done."]) == "Version 2. Then ship it. Done."
