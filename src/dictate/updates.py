"""Check for updates… in the menu. Only when someone clicks it: Dictate never checks by itself.

It asks GitHub for the latest release of Dictate. If there's a newer one:

  Windows  downloads the installer, checks it against the checksum GitHub publishes for it, runs it
           (it closes Dictate, updates it and starts it again; settings, notes and the GPU download
           are kept) - one click.
  Mac      opens the new .dmg in the browser and says what to do (drag it into Applications).

While it runs, the network guard lets GitHub through (and nothing else new); afterwards GitHub is
refused again.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import subprocess
import sys
import tempfile
import urllib.request
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dictate import __version__, offline

log = logging.getLogger(__name__)

REPO = "nicolasrebollo/smart-dictate"
LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
# GitHub's API, its download links, and where those links redirect to.
HOSTS = ("api.github.com", "github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com")
Report = Callable[[str, float | None], None]


@dataclass(frozen=True)
class Asset:
    url: str
    sha256: str  # "" when GitHub didn't publish one
    size: int


@dataclass(frozen=True)
class Release:
    version: str
    page: str
    assets: dict[str, Asset] = field(default_factory=dict)


def parse_version(text: str) -> tuple[int, ...]:
    """'v0.3.0' -> (0, 3, 0). Anything after the numbers (-beta…) is ignored."""
    m = re.match(r"\s*v?(\d+(?:\.\d+)*)", text)
    return tuple(int(x) for x in m[1].split(".")) if m else ()


def is_newer(latest: str, current: str = __version__) -> bool:
    a, b = parse_version(latest), parse_version(current)
    width = max(len(a), len(b))
    return bool(a) and a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


def _apple_silicon() -> bool:
    """True on an M-series Mac, even when this copy runs as Intel code (Rosetta)."""
    try:
        out = subprocess.run(["sysctl", "-n", "hw.optional.arm64"], capture_output=True, text=True, timeout=3)
        return out.stdout.strip() == "1"
    except (OSError, subprocess.SubprocessError):
        import platform

        return platform.machine() == "arm64"


def installer_name(system: str | None = None, apple_silicon: Callable[[], bool] | None = None) -> str | None:
    system = system or sys.platform
    if system == "win32":
        return "Dictate-Windows-Setup.exe"
    if system == "darwin":
        return "Dictate-Mac-AppleSilicon.dmg" if (apple_silicon or _apple_silicon)() else "Dictate-Mac-Intel.dmg"
    return None


def parse_release(data: dict[str, Any]) -> Release:
    assets = {}
    for a in data.get("assets", []):
        digest = a.get("digest") or ""
        sha = digest.removeprefix("sha256:") if digest.startswith("sha256:") else ""
        assets[a["name"]] = Asset(a["browser_download_url"], sha.lower(), int(a.get("size") or 0))
    return Release(str(data.get("tag_name", "")).lstrip("v"), str(data.get("html_url") or RELEASES_PAGE), assets)


def _open(url: str, timeout: float) -> Any:
    from dictate.client import tls_context

    request = urllib.request.Request(
        url, headers={"User-Agent": f"Dictate/{__version__}", "Accept": "application/vnd.github+json"}
    )
    return urllib.request.urlopen(request, timeout=timeout, context=tls_context("wss://"))


def latest_release() -> Release:
    with offline.allowing(*HOSTS), _open(LATEST, timeout=15) as r:
        return parse_release(json.load(r))


def download(asset: Asset, dest: Path, report: Report) -> Path:
    """Download `asset` to `dest`, checking it against its published checksum."""
    if not asset.sha256:
        raise RuntimeError("GitHub didn't publish a checksum for this download, so Dictate won't install it")
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    h = hashlib.sha256()
    done = 0
    with offline.allowing(*HOSTS), _open(asset.url, timeout=60) as r, part.open("wb") as f:
        total = asset.size or int(r.headers.get("Content-Length") or 0)
        while chunk := r.read(1 << 20):
            f.write(chunk)
            h.update(chunk)
            done += len(chunk)
            if total:
                report(f"Downloading the update… {done / 1e6:.0f} of {total / 1e6:.0f} MB", done / total)
    if h.hexdigest() != asset.sha256:
        part.unlink(missing_ok=True)
        raise RuntimeError("the download doesn't match its published checksum (it was damaged or changed)")
    part.replace(dest)
    return dest


def run_windows_installer(path: Path) -> None:
    """Install without questions. The installer closes Dictate first and starts the new one at the end."""
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    subprocess.Popen(
        [str(path), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-"], close_fds=True, creationflags=flags
    )


# -- the menu item ----------------------------------------------------------------------------------------
def check(quit_app: Callable[[], None]) -> None:
    """Menu: Check for updates… (blocks with dialogs; run it off the tray's thread)."""
    from dictate import dialogs

    try:
        release = latest_release()
    except Exception as e:
        log.warning("update check failed: %s", e)
        choice = dialogs.message(
            "Couldn't check for updates",
            f"Dictate couldn't reach GitHub ({getattr(e, 'reason', e)}). Check the internet connection and try "
            f"again, or look on the website:\n\n{RELEASES_PAGE}",
            ("OK", "Open the website"),
            default="OK",
        )
        if choice == "Open the website":
            webbrowser.open(RELEASES_PAGE)
        return
    if not is_newer(release.version):
        dialogs.message("Dictate is up to date", f"You have the latest version, {__version__}.")
        return
    log.info("update available: %s (have %s)", release.version, __version__)
    name = installer_name()
    asset = release.assets.get(name or "")
    new, old = release.version, __version__
    if not getattr(sys, "frozen", False):
        choice = dialogs.message(
            f"Dictate {new} is out",
            f"You have {old}, running from the source code: update with `git pull`, then `uv sync` with the "
            "extras you use.",
            ("OK", "See what's new"),
            default="OK",
        )
        if choice == "See what's new":
            webbrowser.open(release.page)
        return
    if asset is None:
        webbrowser.open(release.page)
        return
    if sys.platform == "win32":
        _update_windows(release, asset, quit_app)
    else:
        _update_mac(release, asset, name or "", quit_app)


def _ask(title: str, text: str, action: str, page: str) -> bool:
    """Later / See what's new / `action`. True if they chose `action`."""
    from dictate import dialogs

    while True:
        choice = dialogs.message(title, text, ("Later", "See what's new", action))
        if choice != "See what's new":
            return choice == action
        webbrowser.open(page)


def _update_windows(release: Release, asset: Asset, quit_app: Callable[[], None]) -> None:
    from dictate import dialogs

    size = f" ({asset.size / 1e6:.0f} MB)" if asset.size else ""
    if not _ask(
        f"Dictate {release.version} is available",
        f"You have {__version__}.\n\nUpdate now? Dictate downloads the update{size}, closes, updates itself and "
        "starts again, in about a minute. Your settings, API keys, notes and downloads are kept.",
        "Update now",
        release.page,
    ):
        return
    dest = Path(tempfile.gettempdir()) / "dictate-update" / "Dictate-Windows-Setup.exe"
    try:
        dialogs.progress("Updating Dictate", lambda report: download(asset, dest, report))
        run_windows_installer(dest)
    except Exception as e:
        log.exception("update failed")
        choice = dialogs.message(
            "The update didn't download",
            f"{e}\n\nTry again later, or download it yourself from the website.",
            ("OK", "Open the website"),
            default="OK",
        )
        if choice == "Open the website":
            webbrowser.open(release.page)
        return
    quit_app()


def _update_mac(release: Release, asset: Asset, name: str, quit_app: Callable[[], None]) -> None:
    from dictate import dialogs

    if not _ask(
        f"Dictate {release.version} is available",
        f"You have {__version__}.\n\nDownload it now? It opens in your browser and goes to your Downloads folder.",
        "Download",
        release.page,
    ):
        return
    webbrowser.open(asset.url)
    choice = dialogs.message(
        "Finish the update",
        f"When {name} has downloaded:\n\n"
        "1.  Quit Dictate (the button below, or Quit Dictate in its menu).\n"
        f"2.  Open {name} from Downloads.\n"
        "3.  Drag Dictate onto Applications and choose Replace.\n"
        "4.  Open Dictate. macOS forgets its permissions after an update: in System Settings › Privacy & "
        "Security, under Accessibility and under Input Monitoring, remove Dictate with – and allow it again.\n\n"
        "Your settings, API keys and notes are kept.",
        ("Later", "Quit Dictate"),
    )
    if choice == "Quit Dictate":
        quit_app()
