# PyInstaller recipe for the Dictate desktop app: the client only (hotkey, microphone, paste, tray).
# No models, no server code. Run packaging/build.py rather than this file directly.
# ruff: noqa
import sys
from pathlib import Path

from dictate import __version__

ROOT = Path(SPECPATH).parent
ICONS = ROOT / "build" / "icons"
MAC = sys.platform == "darwin"

if MAC:
    hidden = ["pystray._darwin", "PyObjCTools.AppHelper", "Quartz", "ApplicationServices", "truststore._macos"]
else:
    hidden = ["pystray._win32", "pynput.keyboard._win32", "pynput.mouse._win32", "truststore._windows"]

# Never needed by the client (some are installed on the PC that also runs the server).
excludes = [
    "numpy", "onnxruntime", "onnx_asr", "onnx", "soundfile", "rapidfuzz", "huggingface_hub", "jiwer",
    "pytest", "dictate.engine", "dictate.server", "dictate.asr", "dictate.llm", "dictate.bench",
    "dictate.pipeline", "dictate.cleanup", "dictate.models",
]
if MAC:
    excludes.append("tkinter")  # dialogs use AppleScript on the Mac

a = Analysis(
    [str(ROOT / "packaging" / "dictate_app.py")],
    pathex=[str(ROOT / "src")],
    hiddenimports=hidden,
    excludes=excludes,
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
            "LSMinimumSystemVersion": "11.0",
            "NSHighResolutionCapable": True,
            "NSMicrophoneUsageDescription": (
                "Dictate listens while you hold the hotkey and sends your voice to your own PC, "
                "which turns it into text."
            ),
        },
    )
