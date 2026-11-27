"""Computer audio as the microphone: block timing, the Mic switching to it, and the server treating it
as something playing (no spoken commands). The real Windows capture is checked when it's available."""

import queue
import sys
import threading
import time
from typing import ClassVar

import pytest

from dictate import mic as mic_module
from dictate import system_audio
from dictate.config import parse_config
from dictate.engine import Engine
from dictate.mic import Mic
from dictate.streaming import ASRWorker
from dictate.system_audio import BLOCK, Blocks, float_to_pcm16, is_system


def test_the_setting_name() -> None:
    assert is_system("computer audio") and is_system("  Computer Audio ")
    assert not is_system("") and not is_system("Microphone (Realtek)") and not is_system(3)


def test_blocks_are_whole_40ms_pieces() -> None:
    out: list[bytes] = []
    b = Blocks(out.append, clock=lambda: 0.0)
    b.add(b"\x01\x00" * 1000)  # 1000 samples: one block, 360 left over
    assert [len(x) for x in out] == [BLOCK * 2]
    b.add(b"\x01\x00" * 280)
    assert [len(x) for x in out] == [BLOCK * 2] * 2


def test_silence_fills_in_while_nothing_plays() -> None:
    now = [0.0]
    out: list[bytes] = []
    b = Blocks(out.append, clock=lambda: now[0])
    now[0] = 0.1
    b.keep_pace()
    assert out == []  # within the slack: the next packet may just be late
    now[0] = 1.12
    b.keep_pace()
    assert sum(len(x) for x in out) // 2 == 16000  # a second of silence for a second of nothing
    assert set(out[0]) == {0}
    b.keep_pace()
    assert sum(len(x) for x in out) // 2 == 16000  # caught up: nothing more


def test_float_samples_become_pcm16() -> None:
    pcm = float_to_pcm16([0.0, 1.0, -1.0, 2.0])
    assert [int.from_bytes(pcm[i : i + 2], "little", signed=True) for i in range(0, 8, 2)] == [0, 32767, -32767, 32767]


class FakeCapture:
    opened: ClassVar[list["FakeCapture"]] = []

    def __init__(self, sink):
        self.sink, self.stopped = sink, False
        FakeCapture.opened.append(self)

    def stop(self):
        self.stopped = True

    def close(self):
        self.stopped = True


@pytest.fixture
def fake_capture(monkeypatch):
    FakeCapture.opened = []
    monkeypatch.setattr(system_audio, "open_capture", FakeCapture)
    monkeypatch.setattr(system_audio, "available", lambda: True)
    return FakeCapture


def test_mic_records_computer_audio_only_while_the_key_is_held(fake_capture) -> None:
    m = Mic("computer audio", keep_open=True)  # keep_open doesn't apply: nothing to pre-roll
    m.open()
    assert m.system and m.name == system_audio.LABEL and fake_capture.opened == []
    got: list[bytes] = []
    m.begin(got.append)
    cap = fake_capture.opened[-1]
    cap.sink(b"\x00\x00" * BLOCK)
    assert got == [b"\x00\x00" * BLOCK]
    m.end()
    assert cap.stopped


def test_switching_between_a_microphone_and_computer_audio(fake_capture, monkeypatch) -> None:
    started = []
    monkeypatch.setattr(Mic, "_start_stream", lambda self: started.append(self.system))
    monkeypatch.setattr(mic_module.sd, "query_devices", lambda *a: {"name": "Default mic"})
    m = Mic("", keep_open=False)
    m.set_device("computer audio")
    assert m.system and m.name == system_audio.LABEL
    m.set_device("")
    assert not m.system and m.name == "Default mic"


def test_computer_audio_not_offered_where_it_cannot_work(monkeypatch) -> None:
    monkeypatch.setattr(system_audio, "available", lambda: False)
    m = Mic("", keep_open=False)
    with pytest.raises(ValueError, match="computer's audio"):
        m.set_device("computer audio")


class _ASR:
    device = "cpu"

    def transcribe(self, audio, **kw):
        raise AssertionError("not used")


class _VAD:
    def reset(self):
        pass


def _engine():
    e = object.__new__(Engine)
    e.cfg = parse_config("")
    e.llm = None
    e.asr = _ASR()
    e.lock = threading.Lock()
    e.worker = ASRWorker(e.asr, e.lock)
    e._vads = queue.Queue()
    e._vads.put(_VAD())
    e._keep_warm = None
    e._active = 0
    e._active_lock = threading.Lock()
    return e


def test_whats_playing_is_not_taken_as_commands() -> None:
    said = "first we tried it new paragraph then we shipped it"
    dictated = _engine().session("en").pipeline.clean_text(said).text
    playing = _engine().session("en", source="computer").pipeline.clean_text(said).text
    blank_line = chr(10) * 2
    assert blank_line in dictated and "new paragraph" not in dictated.lower()  # a command when you dictate
    assert "new paragraph" in playing.lower() and blank_line not in playing  # just words in a video


@pytest.mark.skipif(sys.platform != "win32", reason="Windows loopback")
def test_windows_loopback_delivers_steady_blocks() -> None:
    got: list[bytes] = []
    try:
        cap = system_audio.open_capture(got.append)
    except RuntimeError as e:
        pytest.skip(f"no audio output here: {e}")
    time.sleep(0.6)
    cap.close()
    assert got and {len(b) for b in got} == {BLOCK * 2}
    assert 0.3 <= sum(len(b) for b in got) / 2 / 16000 <= 0.8
