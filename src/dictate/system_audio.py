"""Computer audio as a microphone: what's playing on this computer (a video, a call, a podcast) instead
of what the microphone hears. Pick "Computer audio" under Microphone in the menu; the hotkey then
transcribes whatever is playing.

  Windows  WASAPI loopback of the default speakers/headphones (Windows' own API, no driver, no
           permission prompt). Windows converts the audio to 16 kHz mono for us.
  Mac      ScreenCaptureKit (macOS 13+), audio only. macOS asks once for "Screen & System Audio
           Recording" permission (System Settings › Privacy & Security). Dictate's own sounds are left out.

Both deliver the same thing as the microphone: 16 kHz mono 16-bit PCM in 40 ms blocks, to a callback
that must not block. When nothing is playing, silence keeps arriving (the speech detection needs time
to pass), so a quiet stretch and a pause sound the same.
"""

from __future__ import annotations

import array
import contextlib
import logging
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

DEVICE = "computer audio"  # the [client] input_device value that means this
LABEL = "Computer audio (what's playing)"
SAMPLE_RATE = 16_000
BLOCK = 640  # 40 ms, like the microphone
Sink = Callable[[bytes], None]


def is_system(spec: Any) -> bool:
    return isinstance(spec, str) and spec.strip().lower() == DEVICE


class Blocks:
    """Re-cuts PCM16 bytes of any length into whole 40 ms blocks, and fills gaps with silence so the
    audio keeps pace with the clock (loopback delivers nothing while nothing plays)."""

    def __init__(self, sink: Sink, clock: Callable[[], float] = time.monotonic) -> None:
        self._sink = sink
        self._clock = clock
        self._buf = bytearray()
        self._start = clock()
        self._samples = 0  # handed on so far

    def add(self, pcm: bytes) -> None:
        self._buf += pcm
        self._samples += len(pcm) // 2
        size = BLOCK * 2
        while len(self._buf) >= size:
            self._sink(bytes(self._buf[:size]))
            del self._buf[:size]

    def keep_pace(self, slack_s: float = 0.12) -> None:
        """Called when no audio came: add silence for the time that passed, minus a little slack (the
        next real packet may just be late)."""
        behind = int((self._clock() - self._start - slack_s) * SAMPLE_RATE) - self._samples
        if behind >= BLOCK:
            self.add(bytes(behind // BLOCK * BLOCK * 2))


def available() -> bool:
    if sys.platform == "win32":
        return True
    if sys.platform == "darwin":
        import platform

        try:
            major = int(platform.mac_ver()[0].split(".")[0])
        except ValueError:
            return False
        if major < 13:
            return False
        try:  # (both: a build that can't load CoreMedia would capture silence)
            import CoreMedia  # noqa: F401
            import ScreenCaptureKit  # noqa: F401
        except ImportError:
            log.warning("computer audio unavailable", exc_info=True)
            return False
        return True
    return False


def open_capture(sink: Sink) -> Any:
    """Start capturing; returns an object with stop() and close(). Raises RuntimeError with a message
    for people if it can't."""
    if sys.platform == "win32":
        return WindowsLoopback(sink)
    if sys.platform == "darwin":
        return MacSystemAudio(sink)
    raise RuntimeError("capturing the computer's audio works on Windows and Mac only")


# -- Windows: WASAPI loopback, through COM with ctypes -------------------------------------------------
class WindowsLoopback:
    name = "Computer audio"
    POLL_S = 0.02

    def __init__(self, sink: Sink) -> None:
        self._blocks = Blocks(sink)
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._error: BaseException | None = None
        self._thread = threading.Thread(target=self._run, name="computer-audio", daemon=True)
        self._thread.start()
        self._ready.wait(5)
        if self._error is not None:
            raise RuntimeError(f"couldn't capture the computer's audio: {self._error}") from self._error
        if not self._ready.is_set():
            self._stop.set()
            raise RuntimeError("couldn't capture the computer's audio: Windows didn't start it in time")

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not threading.current_thread():
            self._thread.join(timeout=2)

    def close(self) -> None:
        self.stop()

    def _run(self) -> None:
        import ctypes

        ole32 = ctypes.OleDLL("ole32")
        initialized = False
        try:
            ole32.CoInitializeEx(None, 0)  # COINIT_MULTITHREADED, for this thread
            initialized = True
        except OSError as e:
            if (e.winerror or 0) & 0xFFFFFFFF != 0x80010106:  # RPC_E_CHANGED_MODE: COM is already on
                self._error = e
                self._ready.set()
                return
        try:
            _wasapi_loop(self)
        except BaseException as e:
            if not self._ready.is_set():
                self._error = e
            else:
                log.exception("computer audio capture stopped")
        finally:
            self._ready.set()
            if initialized:
                ole32.CoUninitialize()


def _wasapi_loop(cap: WindowsLoopback) -> None:
    import ctypes
    from ctypes import POINTER, byref, c_int64, c_uint8, c_uint16, c_uint32, c_uint64, c_void_p

    ole32 = ctypes.OleDLL("ole32")

    class GUID(ctypes.Structure):
        _fields_ = [("a", c_uint32), ("b", c_uint16), ("c", c_uint16), ("d", c_uint8 * 8)]

    class WAVEFORMATEX(ctypes.Structure):
        _pack_ = 1
        _fields_ = [
            ("wFormatTag", c_uint16), ("nChannels", c_uint16), ("nSamplesPerSec", c_uint32),
            ("nAvgBytesPerSec", c_uint32), ("nBlockAlign", c_uint16), ("wBitsPerSample", c_uint16),
            ("cbSize", c_uint16),
        ]  # fmt: skip

    def guid(text: str) -> GUID:
        g = GUID()
        ole32.CLSIDFromString(ctypes.c_wchar_p(text), byref(g))
        return g

    def method(obj: c_void_p, index: int, *argtypes: Any) -> Callable[..., Any]:
        vtable = ctypes.cast(obj, POINTER(POINTER(c_void_p)))[0]
        fn = ctypes.WINFUNCTYPE(ctypes.HRESULT, c_void_p, *argtypes)(vtable[index])
        return lambda *args: fn(obj, *args)

    def release(obj: c_void_p) -> None:
        if obj:
            ctypes.WINFUNCTYPE(c_uint32, c_void_p)(ctypes.cast(obj, POINTER(POINTER(c_void_p)))[0][2])(obj)

    enumerator, device, client, capture = c_void_p(), c_void_p(), c_void_p(), c_void_p()
    try:
        ole32.CoCreateInstance(
            byref(guid("{BCDE0395-E52F-467C-8E3D-C4579291692E}")), None, 0x17,  # MMDeviceEnumerator, CLSCTX_ALL
            byref(guid("{A95664D2-9614-4F35-A746-DE8DB63617E6}")), byref(enumerator),
        )  # fmt: skip
        try:  # IMMDeviceEnumerator::GetDefaultAudioEndpoint(eRender, eConsole)
            method(enumerator, 4, c_uint32, c_uint32, POINTER(c_void_p))(0, 0, byref(device))
        except OSError as e:
            raise RuntimeError("this computer has no speakers or headphones turned on") from e
        # IMMDevice::Activate(IID_IAudioClient)
        method(device, 3, POINTER(GUID), c_uint32, c_void_p, POINTER(c_void_p))(
            byref(guid("{1CB9AD4C-DBFA-4C32-B178-C2F568A703B2}")), 0x17, None, byref(client)
        )
        fmt = WAVEFORMATEX(1, 1, SAMPLE_RATE, SAMPLE_RATE * 2, 2, 16, 0)  # what we want: 16 kHz mono PCM16
        loopback, autoconvert, good_resampling = 0x00020000, 0x80000000, 0x08000000
        # IAudioClient::Initialize(shared, flags, 200 ms buffer, 0, format, None)
        method(client, 3, c_uint32, c_uint32, c_int64, c_int64, POINTER(WAVEFORMATEX), c_void_p)(
            0, loopback | autoconvert | good_resampling, 2_000_000, 0, byref(fmt), None
        )
        # IAudioClient::GetService(IID_IAudioCaptureClient)
        method(client, 14, POINTER(GUID), POINTER(c_void_p))(
            byref(guid("{C8ADBD64-E71E-48A0-A4DE-185C395CD317}")), byref(capture)
        )
        next_packet = method(capture, 5, POINTER(c_uint32))
        get_buffer = method(
            capture, 3, POINTER(POINTER(c_uint8)), POINTER(c_uint32), POINTER(c_uint32),
            POINTER(c_uint64), POINTER(c_uint64),
        )  # fmt: skip
        release_buffer = method(capture, 4, c_uint32)
        method(client, 10)()  # Start
        cap._blocks = Blocks(cap._blocks._sink)  # the clock starts now
        cap._ready.set()
        packet, frames, flags = c_uint32(), c_uint32(), c_uint32()
        data = POINTER(c_uint8)()
        while not cap._stop.is_set():
            got = False
            next_packet(byref(packet))
            while packet.value:
                get_buffer(byref(data), byref(frames), byref(flags), None, None)
                n = frames.value
                silent = flags.value & 0x2  # AUDCLNT_BUFFERFLAGS_SILENT
                pcm = bytes(n * 2) if silent else ctypes.string_at(data, n * 2)
                release_buffer(n)
                cap._blocks.add(pcm)
                got = True
                next_packet(byref(packet))
            if not got:
                cap._blocks.keep_pace()
            cap._stop.wait(WindowsLoopback.POLL_S)
        method(client, 11)()  # Stop
    finally:
        for obj in (capture, client, device, enumerator):
            release(obj)


# -- Mac: ScreenCaptureKit (audio only) -------------------------------------------------------------
class MacSystemAudio:
    """An SCStream of the main display with audio on and video as small and rare as macOS allows.

    Needs macOS 13+ and the Screen & System Audio Recording permission (macOS asks the first time)."""

    name = "Computer audio"

    def __init__(self, sink: Sink) -> None:
        import ScreenCaptureKit as SCK

        self._blocks = Blocks(sink)
        self._lock = threading.Lock()
        self._stream: Any = None
        content = _mac_wait(SCK.SCShareableContent.getShareableContentWithCompletionHandler_, "find the screen")
        displays = content.displays() if content is not None else []
        if not displays:
            raise RuntimeError(
                "macOS didn't allow it: turn on Dictate under System Settings › Privacy & Security › "
                "Screen & System Audio Recording, then quit and reopen Dictate"
            )
        filt = SCK.SCContentFilter.alloc().initWithDisplay_excludingWindows_(displays[0], [])
        cfg = SCK.SCStreamConfiguration.alloc().init()
        cfg.setCapturesAudio_(True)
        cfg.setSampleRate_(SAMPLE_RATE)
        cfg.setChannelCount_(1)
        cfg.setExcludesCurrentProcessAudio_(True)  # not Dictate's own beeps
        cfg.setWidth_(2)
        cfg.setHeight_(2)
        with contextlib.suppress(Exception):
            import CoreMedia

            cfg.setMinimumFrameInterval_(CoreMedia.CMTimeMake(1, 1))  # one tiny frame a second
        self._output = _mac_output_class().alloc().init()
        self._output.sink = self._audio
        stream = SCK.SCStream.alloc().initWithFilter_configuration_delegate_(filt, cfg, None)
        ok, err = _mac_add_output(stream, self._output)
        if not ok:
            raise RuntimeError(f"couldn't capture the computer's audio ({err})")
        _mac_wait(stream.startCaptureWithCompletionHandler_, "start capturing", returns_value=False)
        self._stream = stream
        self._quiet = threading.Event()
        threading.Thread(target=self._pace, name="computer-audio-pace", daemon=True).start()

    def _audio(self, pcm: bytes) -> None:
        with self._lock:
            self._blocks.add(pcm)

    def _pace(self) -> None:
        while not self._quiet.wait(0.05):
            with self._lock:
                self._blocks.keep_pace()

    def stop(self) -> None:
        self._quiet.set()
        stream, self._stream = self._stream, None
        if stream is not None:
            with contextlib.suppress(Exception):
                _mac_wait(stream.stopCaptureWithCompletionHandler_, "stop", returns_value=False, timeout=3)

    def close(self) -> None:
        self.stop()


def _mac_wait(call: Callable[..., Any], what: str, *, returns_value: bool = True, timeout: float = 10) -> Any:
    """Call a ScreenCaptureKit method that answers through a completion handler, and wait for it."""
    done = threading.Event()
    result: list[Any] = [None, None]

    if returns_value:

        def handler(value: Any, error: Any) -> None:
            result[0], result[1] = value, error
            done.set()
    else:

        def handler(error: Any) -> None:  # type: ignore[misc]
            result[1] = error
            done.set()

    call(handler)
    if not done.wait(timeout):
        raise RuntimeError(f"macOS didn't answer when Dictate tried to {what}")
    if result[1] is not None:
        raise RuntimeError(
            "macOS didn't allow it: turn on Dictate under System Settings › Privacy & Security › "
            "Screen & System Audio Recording, then quit and reopen Dictate "
            f"({result[1].localizedDescription() if hasattr(result[1], 'localizedDescription') else result[1]})"
        )
    return result[0]


def _mac_add_output(stream: Any, output: Any) -> tuple[bool, Any]:
    import ScreenCaptureKit as SCK

    got = stream.addStreamOutput_type_sampleHandlerQueue_error_(output, SCK.SCStreamOutputTypeAudio, None, None)
    if isinstance(got, tuple):
        return bool(got[0]), got[1]
    return bool(got), None


_OUTPUT_CLASS: Any = None


def _mac_output_class() -> Any:
    """The SCStreamOutput that receives the audio (made once: an Objective-C class can't be redefined)."""
    global _OUTPUT_CLASS
    if _OUTPUT_CLASS is not None:
        return _OUTPUT_CLASS
    import objc
    import ScreenCaptureKit as SCK
    from Foundation import NSObject

    try:  # PyObjC knows the method's signature either way; the protocol just makes it official
        protocols = [objc.protocolNamed("SCStreamOutput")]
    except Exception:
        protocols = []

    class DictateAudioOutput(NSObject, protocols=protocols):
        sink: Any = None

        def stream_didOutputSampleBuffer_ofType_(self, stream: Any, buffer: Any, kind: int) -> None:
            if kind != SCK.SCStreamOutputTypeAudio or self.sink is None:
                return
            try:
                pcm = _mac_pcm16(buffer)
            except Exception:
                log.debug("couldn't read an audio buffer", exc_info=True)
                return
            if pcm:
                self.sink(pcm)

    _OUTPUT_CLASS = DictateAudioOutput
    return _OUTPUT_CLASS


def _mac_pcm16(buffer: Any) -> bytes:
    """A CMSampleBuffer of 32-bit float mono audio -> PCM16 bytes."""
    import CoreMedia

    block = CoreMedia.CMSampleBufferGetDataBuffer(buffer)
    if block is None:
        return b""
    length = CoreMedia.CMBlockBufferGetDataLength(block)
    got = CoreMedia.CMBlockBufferCopyDataBytes(block, 0, length, None)
    raw = got[1] if isinstance(got, tuple) else got
    if not isinstance(raw, bytes | bytearray | memoryview):
        raw = bytes(raw)
    floats = array.array("f")
    floats.frombytes(bytes(raw)[: len(raw) // 4 * 4])
    return float_to_pcm16(floats)


def float_to_pcm16(samples: Any) -> bytes:
    out = array.array("h", (max(-32768, min(32767, int(s * 32767))) for s in samples))
    if sys.byteorder == "big":
        out.byteswap()
    return out.tobytes()
