"""Microphone capture for the client: 16 kHz mono 16-bit. No numpy: the client just forwards raw
PCM bytes to the server. The PortAudio callback only copies audio; it never blocks.

Two modes ([client] keep_mic_open):
  open all the time   keeps the last 300 ms so the first syllable is never clipped, but the OS shows
                      its "microphone in use" indicator constantly (orange dot on macOS)
  open while talking  the mic (and the indicator) is only on while you hold the key; the mic needs
                      ~0.1 s to start, so the very start of the first word can occasionally be clipped
"""

from __future__ import annotations

import array
import collections
import logging
import math
import sys
import threading
from collections.abc import Callable
from typing import Any

import sounddevice as sd

log = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
BLOCK = 640  # 40 ms


def resolve_device(spec: str | int) -> int | None:
    """'' -> default device; int -> index; str -> first input device whose name contains it."""
    if spec == "" or spec is None:
        return None
    if isinstance(spec, int):
        return spec
    if spec.isdigit():
        return int(spec)
    for i, dev in enumerate(sd.query_devices()):
        if dev["max_input_channels"] > 0 and spec.lower() in dev["name"].lower():
            return i
    raise ValueError(f"no microphone matching {spec!r} (run `dictate devices`)")


def list_input_devices() -> list[tuple[int, str, bool]]:
    default_in = sd.default.device[0]
    devices = enumerate(sd.query_devices())
    return [(i, dev["name"], i == default_in) for i, dev in devices if dev["max_input_channels"] > 0]


def microphone_names() -> list[str]:
    """Input devices of the default sound system, by name, for a menu. (Windows lists every mic once
    per sound system: MME, DirectSound, WASAPI...; only the default system's copy is shown.)"""
    try:
        default_api = sd.query_hostapis(sd.default.hostapi)["name"] if sd.default.hostapi >= 0 else None
    except Exception:
        default_api = None
    names: list[str] = []
    for dev in sd.query_devices():
        if dev["max_input_channels"] <= 0:
            continue
        if default_api is not None and sd.query_hostapis(dev["hostapi"])["name"] != default_api:
            continue
        name = str(dev["name"]).strip()
        if name and name not in names and "mapper" not in name.lower() and "primary sound" not in name.lower():
            names.append(name)
    return names


def level_warning(pcm: bytes | Any) -> str | None:
    """A muted/blocked mic delivers (near) digital silence; a quiet room is around -65 dBFS.
    Accepts PCM16 bytes, or a float array in [-1, 1]."""
    if isinstance(pcm, bytes | bytearray):
        samples = array.array("h")
        samples.frombytes(bytes(pcm[: len(pcm) - len(pcm) % 2]))
        if sys.byteorder == "big":
            samples.byteswap()
        values = [s / 32768 for s in samples]
    else:
        values = [float(v) for v in pcm]
    if not values:
        return None
    if sum(1 for v in values if abs(v) >= 0.99) / len(values) > 0.001:
        return "your microphone is clipping (too loud): lower its input level"
    rms = math.sqrt(sum(v * v for v in values) / len(values))
    if 20 * math.log10(max(rms, 1e-9)) < -85:
        return "the microphone is silent: check that this app is allowed to use the microphone"
    return None


class Mic:
    """While a recording is active, PCM16 blocks go to `sink`. With keep_open, the stream runs all the
    time and the last `preroll_ms` are prepended to the next recording."""

    def __init__(self, device: str | int = "", preroll_ms: int = 300, *, keep_open: bool = True) -> None:
        self._device = resolve_device(device)
        self.keep_open = keep_open
        self._preroll: collections.deque[bytes] = collections.deque(
            maxlen=max(1, round(preroll_ms * SAMPLE_RATE / 1000 / BLOCK))
        )
        self._sink: Callable[[bytes], None] | None = None
        self._lock = threading.Lock()  # protects _sink / _preroll
        self._stream_lock = threading.Lock()  # protects opening / closing the stream
        self._stream: sd.RawInputStream | None = None
        self.name = ""

    def _start_stream(self) -> None:
        with self._stream_lock:
            if self._stream is not None:
                return
            stream = sd.RawInputStream(
                samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=BLOCK, device=self._device,
                latency="low", callback=self._callback,
            )  # fmt: skip
            stream.start()
            self._stream = stream
            self.name = sd.query_devices(stream.device)["name"]

    def _stop_stream(self) -> None:
        with self._stream_lock:
            stream, self._stream = self._stream, None
        if stream is not None:
            stream.stop()
            stream.close()

    def open(self) -> None:
        if self.keep_open:
            self._start_stream()
        else:  # just check the device now, so a wrong setting shows up at startup
            dev = self._device if self._device is not None else sd.default.device[0]
            self.name = sd.query_devices(dev)["name"]

    def close(self) -> None:
        self._stop_stream()

    def set_device(self, spec: str | int) -> None:
        """Switch microphones ('' = the system default). Raises ValueError if there's no such mic."""
        device = resolve_device(spec)
        was_open = self._stream is not None
        self._stop_stream()
        self._device = device
        if was_open:
            self._start_stream()
        else:
            self.name = sd.query_devices(device if device is not None else sd.default.device[0])["name"]

    def request_access(self) -> None:
        """Open the mic for a moment, so macOS asks for microphone permission now rather than in the
        middle of the first dictation."""
        if self._stream is not None:
            return
        try:
            self._start_stream()
            threading.Event().wait(0.3)
        except Exception:
            log.debug("microphone access check failed", exc_info=True)
        finally:
            if not self.keep_open:
                self._stop_stream()

    def _callback(self, indata: Any, frames: int, time_info: Any, status: sd.CallbackFlags) -> None:
        block = bytes(indata)  # little-endian int16 on every platform we support
        with self._lock:
            sink = self._sink
            if sink is None:
                self._preroll.append(block)
        if sink is not None:
            sink(block)

    def begin(self, sink: Callable[[bytes], None]) -> None:
        """Start sending audio to `sink` (pre-roll first). `sink` must not block."""
        with self._lock:
            for b in self._preroll:
                sink(b)
            self._preroll.clear()
            self._sink = sink
        if not self.keep_open:
            try:
                self._start_stream()
            except Exception:
                log.exception("could not open the microphone")

    def end(self) -> None:
        with self._lock:
            self._sink = None
        if not self.keep_open:
            self._stop_stream()  # the OS "microphone in use" indicator goes away
