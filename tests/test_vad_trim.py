import numpy as np

from dictate.asr.parakeet import pad_to_bucket
from dictate.vad import FRAME, SAMPLE_RATE, speech_flags, split_at_pauses, trim


def make(probs: list[float]) -> tuple[np.ndarray, np.ndarray]:
    audio = np.arange(len(probs) * FRAME, dtype=np.float32)  # sample i has value i: easy to check
    return audio, np.asarray(probs, dtype=np.float32)


def test_no_speech_returns_none() -> None:
    audio, probs = make([0.1] * 50)
    assert trim(audio, probs) is None


def test_too_little_speech_returns_none() -> None:
    audio, probs = make([0.0] * 20 + [0.9] * 2 + [0.0] * 20)  # 64 ms < 150 ms
    assert trim(audio, probs, min_speech_ms=150) is None


def test_trims_leading_and_trailing_silence_with_padding() -> None:
    audio, probs = make([0.0] * 30 + [0.9] * 20 + [0.0] * 30)
    res = trim(audio, probs, pad_ms=64, max_pause_ms=10_000)  # pad = 2 frames
    assert res is not None
    assert res.audio[0] == 28 * FRAME
    assert len(res.audio) == (20 + 4) * FRAME
    assert len(res.probs) == 24
    assert abs(res.speech_ms - 20 * 32) < 1e-6


def test_long_internal_pause_is_shortened() -> None:
    audio, probs = make([0.9] * 10 + [0.0] * 100 + [0.9] * 10)
    res = trim(audio, probs, pad_ms=0, max_pause_ms=320)  # 10 frames max
    assert res is not None
    assert len(res.audio) == (10 + 10 + 10) * FRAME
    assert len(res.probs) == 30


def test_short_pause_kept_intact() -> None:
    audio, probs = make([0.9] * 10 + [0.0] * 5 + [0.9] * 10)
    res = trim(audio, probs, pad_ms=0, max_pause_ms=320)
    assert res is not None
    np.testing.assert_array_equal(res.audio, audio)


def test_hysteresis_keeps_dips_inside_speech() -> None:
    flags = speech_flags([0.0, 0.6, 0.4, 0.4, 0.2, 0.0], threshold=0.5)
    assert flags.tolist() == [False, True, True, True, False, False]


def test_sub_frame_tail_kept_when_speech_reaches_end() -> None:
    audio = np.ones(10 * FRAME + 100, dtype=np.float32)
    res = trim(audio, [0.9] * 10, pad_ms=0)
    assert res is not None and len(res.audio) == len(audio)


def test_split_short_audio_untouched() -> None:
    audio = np.zeros(SAMPLE_RATE * 5, dtype=np.float32)
    assert len(split_at_pauses(audio, np.ones(160), 30)) == 1


def test_split_cuts_at_quietest_frame() -> None:
    n_frames = int(50 * SAMPLE_RATE / FRAME)
    probs = np.ones(n_frames, dtype=np.float32)
    quiet = int(25 * SAMPLE_RATE / FRAME)
    probs[quiet] = 0.0
    audio = np.zeros(n_frames * FRAME, dtype=np.float32)
    chunks = split_at_pauses(audio, probs, 30)
    assert len(chunks) == 2
    assert len(chunks[0]) == quiet * FRAME
    assert sum(len(c) for c in chunks) == len(audio)


def test_split_respects_max_length() -> None:
    audio = np.zeros(SAMPLE_RATE * 95, dtype=np.float32)
    chunks = split_at_pauses(audio, np.ones(len(audio) // FRAME), 30)
    assert all(len(c) <= 30 * SAMPLE_RATE for c in chunks)
    assert sum(len(c) for c in chunks) == len(audio)


def test_pad_to_bucket() -> None:
    assert len(pad_to_bucket(np.ones(100, np.float32))) == SAMPLE_RATE // 2
    assert len(pad_to_bucket(np.ones(SAMPLE_RATE, np.float32))) == SAMPLE_RATE
    assert len(pad_to_bucket(np.ones(SAMPLE_RATE + 1, np.float32))) == SAMPLE_RATE * 3 // 2


def test_level_warnings() -> None:
    from dictate.mic import level_warning

    quiet_room = (np.random.default_rng(0).standard_normal(16000) * 0.0005).astype(np.float32)  # ~ -66 dBFS
    assert level_warning(quiet_room) is None
    assert level_warning((quiet_room * 32767).astype("<i2").tobytes()) is None  # PCM16 bytes, as the client has
    assert "silent" in (level_warning(bytes(32000)) or "")
    assert "clipping" in (level_warning(np.ones(16000, np.float32)) or "")
