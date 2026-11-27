# PyInstaller recipe for the Dictate desktop app. Run packaging/build.py rather than this file directly.
#
# The app holds the client (hotkey, microphone, paste, tray) and the engine for online mode (speech
# detection on the CPU, spoken commands, formatting; the speech recognition and the AI cleanup run on
# the online service). It holds no speech or cleanup models. On Windows it also carries what local
# mode needs to set itself up (uv, a Dictate wheel, the pinned requirements), from build/runtime.
# ruff: noqa
import sys
from pathlib import Path

from dictate import __version__

ROOT = Path(SPECPATH).parent
BUILD = ROOT / "build"
ICONS = BUILD / "icons"
MAC = sys.platform == "darwin"

hidden = [
    "dictate.cloud.speech", "dictate.cloud.chat", "dictate.cloud.claude", "dictate.cloud.keys",
    "dictate.engine", "dictate.server", "dictate.modes", "dictate.notes.window", "dictate.updates", "sqlite3",
]
if MAC:
    hidden += ["pystray._darwin", "PyObjCTools.AppHelper", "Quartz", "ApplicationServices", "AppKit",
               "ScreenCaptureKit", "CoreMedia", "dictate.system_audio",
               "truststore._macos", "keyring.backends.macOS"]
else:
    hidden += ["pystray._win32", "pynput.keyboard._win32", "pynput.mouse._win32", "truststore._windows",
               "keyring.backends.Windows"]

# Only the PC that runs the models needs these (local mode installs them separately, see local_gpu.py).
excludes = [
    "onnx_asr", "onnx", "soundfile", "huggingface_hub", "jiwer", "pytest", "dictate.bench",
    "dictate.asr.parakeet", "nvidia", "onnxruntime.transformers", "onnxruntime.quantization",
    "onnxruntime.tools", "sympy", "matplotlib", "IPython",
]
datas = [(str(BUILD / "silero" / "silero_vad.onnx"), "silero")]  # speech detection for online mode
if not MAC:
    datas.append((str(BUILD / "runtime"), "runtime"))  # local mode's setup (packaging/build.py)

a = Analysis(
    [str(ROOT / "packaging" / "dictate_app.py")],
    pathex=[str(ROOT / "src")],
    hiddenimports=hidden,
    excludes=excludes,
    datas=datas,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Dictate",
    console=False,
    icon=str(ICONS / ("dictate.icns" if MAC else "dictate.ico")),
)
coll = COLLECT(exe, a.binaries, a.datas, name="Dictate")

if MAC:
    app = BUNDLE(
        coll,
        name="Dictate.app",
        icon=str(ICONS / "dictate.icns"),
        bundle_identifier="com.dictate.app",
        version=__version__,
        info_plist={
            "CFBundleDisplayName": "Dictate",
            "CFBundleShortVersionString": __version__,
            "LSUIElement": True,  # lives in the menu bar, no Dock icon
            "LSMinimumSystemVersion": "13.0",  # onnxruntime (speech detection) needs macOS 13
            "NSHighResolutionCapable": True,
            "NSMicrophoneUsageDescription": (
                "Dictate listens while you hold the hotkey and turns your voice into text, on the PC you "
                "connected to or the online service you chose."
            ),
        },
    )
