"""Build the Dictate desktop app for the computer you run this on:

  Windows -> Dictate-Windows-Setup.exe   (an installer: no admin rights needed)
  macOS   -> Dictate-Mac.dmg             (drag Dictate into Applications)

    uv sync --no-dev --group build --extra cloud
    uv run --no-sync python packaging/build.py [--out PATH]

A Mac build has to be made on a Mac. The GitHub workflow (.github/workflows/build-apps.yml) builds
all of them in the cloud, so you don't need a Mac yourself.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
DIST = ROOT / "dist"
MAC = sys.platform == "darwin"


def make_icons() -> Path:
    """The app icon: the tray's microphone on a dark rounded square."""
    sys.path.insert(0, str(ROOT / "src"))
    from PIL import Image, ImageDraw

    from dictate.tray import icon_image

    out = BUILD / "icons"
    out.mkdir(parents=True, exist_ok=True)
    size = 1024
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    pad = 0 if not MAC else 100  # macOS icons leave a margin around the square
    ImageDraw.Draw(img).rounded_rectangle(
        (pad, pad, size - pad, size - pad), radius=(size - 2 * pad) * 0.22, fill=(32, 36, 48, 255)
    )
    mic = icon_image("ready", round((size - 2 * pad) * 0.72))
    img.alpha_composite(mic, ((size - mic.width) // 2, (size - mic.height) // 2))
    img.save(out / "dictate.png")
    img.save(out / "dictate.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    img.save(out / "dictate.icns")
    return out


def silero() -> Path:
    """The speech detector online mode runs on this computer (2 MB, MIT), hash-checked."""
    sys.path.insert(0, str(ROOT / "src"))
    import hashlib

    from dictate.models import SILERO_SHA256, SILERO_URL

    dest = BUILD / "silero" / "silero_vad.onnx"
    if not (dest.is_file() and hashlib.sha256(dest.read_bytes()).hexdigest() == SILERO_SHA256):
        dest.parent.mkdir(parents=True, exist_ok=True)
        from dictate.models import _download

        _download(SILERO_URL, dest)
        if hashlib.sha256(dest.read_bytes()).hexdigest() != SILERO_SHA256:
            dest.unlink()
            raise SystemExit("Silero VAD download doesn't match its checksum")
    return dest


def _uv() -> str:
    found = os.environ.get("UV") or shutil.which("uv")
    if not found:
        raise SystemExit("uv not found (needed to bundle local mode): install it, or set UV to its path")
    return found


def local_mode_bundle() -> Path:
    """What the Windows app needs to set up local mode by itself (see src/dictate/local_gpu.py): uv, this
    version of Dictate as a wheel, and the server's requirements pinned (with hashes) from uv.lock."""
    uv = _uv()
    out = BUILD / "runtime"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    shutil.copy2(uv, out / "uv.exe")
    subprocess.run([uv, "build", "--wheel", "--out-dir", str(out)], check=True, cwd=ROOT)
    (out / ".gitignore").unlink(missing_ok=True)  # uv build leaves one in its output folder
    subprocess.run(
        [uv, "export", "--frozen", "--extra", "server", "--no-dev", "--no-emit-project", "--format",
         "requirements-txt", "--output-file", str(out / "requirements.txt")],
        check=True, cwd=ROOT,
    )  # fmt: skip
    return out


def pyinstaller() -> Path:
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", str(BUILD / "dist"),
         "--workpath", str(BUILD / "work"), str(ROOT / "packaging" / "dictate.spec")],
        check=True, cwd=ROOT,
    )  # fmt: skip
    return BUILD / "dist" / ("Dictate.app" if MAC else "Dictate")


def _iscc() -> str | None:
    found = shutil.which("iscc")
    if found:
        return found
    for base in (os.environ.get("PROGRAMFILES(X86)", ""), os.environ.get("PROGRAMFILES", "")):
        candidate = Path(base) / "Inno Setup 6" / "ISCC.exe"
        if base and candidate.exists():
            return str(candidate)
    return None


def windows_installer(app_dir: Path, out: Path) -> Path:
    from dictate import __version__

    iscc = _iscc()
    if iscc is None:  # no Inno Setup: a zip still works (unzip, run Dictate.exe)
        print("Inno Setup not found (https://jrsoftware.org/isinfo.php): making a .zip instead.")
        zipped = shutil.make_archive(str(out.with_suffix("")), "zip", app_dir.parent, app_dir.name)
        return Path(zipped)
    subprocess.run(
        [iscc, f"/DVersion={__version__}", f"/DSourceDir={app_dir}", f"/DIconFile={BUILD / 'icons' / 'dictate.ico'}",
         f"/O{out.parent}", f"/F{out.stem}", str(ROOT / "packaging" / "windows" / "dictate.iss")],
        check=True,
    )  # fmt: skip
    return out


def mac_dmg(app: Path, out: Path) -> Path:
    # Every piece of the app must be signed to run on Apple silicon; without an Apple developer ID
    # this is an "ad-hoc" signature (people confirm the first open once, see QUICKSTART.md).
    subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(app)], check=True)
    stage = BUILD / "dmg"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    shutil.copytree(app, stage / app.name, symlinks=True)
    (stage / "Applications").symlink_to("/Applications")
    out.unlink(missing_ok=True)
    subprocess.run(
        ["hdiutil", "create", "-volname", "Dictate", "-srcfolder", str(stage), "-ov", "-format", "UDZO", str(out)],
        check=True,
    )
    return out


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    default = DIST / ("Dictate-Mac.dmg" if MAC else "Dictate-Windows-Setup.exe")
    p.add_argument("--out", type=Path, default=default, help=f"output file (default: {default})")
    args = p.parse_args()
    out = args.out.resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    make_icons()
    silero()
    if not MAC:
        local_mode_bundle()
    app = pyinstaller()
    result = mac_dmg(app, out) if MAC else windows_installer(app, out)
    print(f"\nBuilt {result}")


if __name__ == "__main__":
    main()
