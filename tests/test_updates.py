"""Check for updates…: versions, the right download, checksums, and that GitHub is let through the
network guard only while the check runs (no network needed)."""

import hashlib
import socket
import sys

import pytest

from dictate import offline, updates
from dictate.updates import Asset, Release, download, installer_name, is_newer, parse_release, parse_version


def test_versions() -> None:
    assert parse_version("v0.3.0") == (0, 3, 0)
    assert parse_version("1.2") == (1, 2)
    assert parse_version("0.4.0-beta") == (0, 4, 0)
    assert is_newer("0.3.1", "0.3.0")
    assert is_newer("v0.10.0", "0.9.9")
    assert is_newer("1.0", "0.9.9")
    assert not is_newer("0.3.0", "0.3.0")
    assert not is_newer("0.3", "0.3.0")
    assert not is_newer("0.2.9", "0.3.0")
    assert not is_newer("", "0.3.0")


def test_the_right_download_for_this_computer() -> None:
    assert installer_name("win32") == "Dictate-Windows-Setup.exe"
    assert installer_name("darwin", lambda: True) == "Dictate-Mac-AppleSilicon.dmg"
    assert installer_name("darwin", lambda: False) == "Dictate-Mac-Intel.dmg"
    assert installer_name("linux") is None


def test_reads_githubs_release() -> None:
    rel = parse_release({
        "tag_name": "v0.3.0",
        "html_url": "https://github.com/x/y/releases/tag/v0.3.0",
        "assets": [
            {"name": "Dictate-Windows-Setup.exe", "browser_download_url": "https://github.com/a.exe",
             "digest": "sha256:ABC", "size": 5},
            {"name": "SHA256SUMS.txt", "browser_download_url": "https://github.com/s.txt", "digest": None},
        ],
    })  # fmt: skip
    assert rel.version == "0.3.0"
    assert rel.assets["Dictate-Windows-Setup.exe"] == Asset("https://github.com/a.exe", "abc", 5)
    assert rel.assets["SHA256SUMS.txt"].sha256 == ""


def test_download_checks_the_checksum(tmp_path) -> None:
    src = tmp_path / "setup.exe"
    src.write_bytes(b"installer" * 1000)
    good = hashlib.sha256(src.read_bytes()).hexdigest()
    seen = []
    out = download(
        Asset(src.as_uri(), good, src.stat().st_size), tmp_path / "dl" / "s.exe", lambda t, f: seen.append(f)
    )
    assert out.read_bytes() == src.read_bytes() and seen[-1] == 1.0
    with pytest.raises(RuntimeError, match="checksum"):
        download(Asset(src.as_uri(), "0" * 64, 0), tmp_path / "dl" / "bad.exe", lambda *_: None)
    assert not (tmp_path / "dl" / "bad.exe").exists()
    assert not (tmp_path / "dl" / "bad.exe.part").exists()
    with pytest.raises(RuntimeError, match="checksum"):  # none published: refuse
        download(Asset(src.as_uri(), "", 0), tmp_path / "dl" / "none.exe", lambda *_: None)


def test_github_gets_through_the_guard_only_while_checking(monkeypatch) -> None:
    fake = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.7", 443))]
    monkeypatch.setattr(offline, "_original_getaddrinfo", lambda *a, **k: fake)
    offline.install_network_guard(allow_hosts=["pc.example.ts.net"])
    try:
        with pytest.raises(offline.NetworkBlockedError):
            socket.getaddrinfo("api.github.com", 443)
        with offline.allowing(*updates.HOSTS):
            assert socket.getaddrinfo("api.github.com", 443) == fake
            with pytest.raises(offline.NetworkBlockedError):
                socket.getaddrinfo("example.com", 443)  # only GitHub
        with pytest.raises(offline.NetworkBlockedError):
            socket.getaddrinfo("api.github.com", 443)
        s = socket.socket()
        try:
            with pytest.raises(offline.NetworkBlockedError):  # its address is forgotten again too
                s.connect(("192.0.2.7", 443))
        finally:
            s.close()
        assert socket.getaddrinfo("pc.example.ts.net", 443) == fake  # your PC is still allowed
    finally:
        offline.uninstall_network_guard()


class Dialogs:
    def __init__(self, *answers: str | None) -> None:
        self.answers = list(answers)
        self.shown: list[str] = []

    def message(self, title, text, buttons=("OK",), default=""):
        self.shown.append(title)
        return self.answers.pop(0) if self.answers else buttons[0]

    def progress(self, title, task):
        return task(lambda *_: None)


@pytest.fixture
def ui(monkeypatch):
    def make(*answers, latest="9.9.9", platform="win32", frozen=True):
        d = Dialogs(*answers)
        monkeypatch.setattr("dictate.dialogs.message", d.message)
        monkeypatch.setattr("dictate.dialogs.progress", d.progress)
        monkeypatch.setattr(sys, "platform", platform)
        monkeypatch.setattr(sys, "frozen", frozen, raising=False)
        assets = {name: Asset(f"https://github.com/{name}", "ab" * 32, 1) for name in (
            "Dictate-Windows-Setup.exe", "Dictate-Mac-AppleSilicon.dmg", "Dictate-Mac-Intel.dmg")}  # fmt: skip
        monkeypatch.setattr(updates, "latest_release", lambda: Release(latest, "https://github.com/r", assets))
        monkeypatch.setattr(updates, "_apple_silicon", lambda: True)
        opened: list[str] = []
        monkeypatch.setattr(updates.webbrowser, "open", opened.append)
        return d, opened

    return make


def test_up_to_date(ui) -> None:
    d, _ = ui(latest=updates.__version__)
    quit_calls = []
    updates.check(lambda: quit_calls.append(1))
    assert d.shown == ["Dictate is up to date"] and not quit_calls


def test_windows_downloads_installs_and_quits(ui, monkeypatch) -> None:
    ui("Update now")
    got = {}
    monkeypatch.setattr(updates, "download", lambda asset, dest, report: got.setdefault("dl", (asset.url, dest)))
    monkeypatch.setattr(updates, "run_windows_installer", lambda path: got.setdefault("ran", path))
    quit_calls = []
    updates.check(lambda: quit_calls.append(1))
    assert got["dl"][0].endswith("Dictate-Windows-Setup.exe") and got["ran"] == got["dl"][1]
    assert quit_calls == [1]


def test_windows_later_does_nothing(ui, monkeypatch) -> None:
    ui("Later")
    monkeypatch.setattr(updates, "download", lambda *a: pytest.fail("downloaded"))
    updates.check(lambda: pytest.fail("quit"))


def test_windows_failed_download_keeps_dictate_running(ui, monkeypatch) -> None:
    d, _ = ui("Update now", "OK")

    def broken(*_):
        raise RuntimeError("the download doesn't match its published checksum")

    monkeypatch.setattr(updates, "download", broken)
    monkeypatch.setattr(updates, "run_windows_installer", lambda path: pytest.fail("ran"))
    updates.check(lambda: pytest.fail("quit"))
    assert d.shown[-1] == "The update didn't download"


def test_mac_opens_the_right_dmg(ui) -> None:
    d, opened = ui("See what's new", "Download", "Quit Dictate", platform="darwin")
    quit_calls = []
    updates.check(lambda: quit_calls.append(1))
    assert opened == ["https://github.com/r", "https://github.com/Dictate-Mac-AppleSilicon.dmg"]
    assert d.shown[-1] == "Finish the update" and quit_calls == [1]


def test_source_checkout_is_told_to_git_pull(ui) -> None:
    d, opened = ui("OK", frozen=False)
    updates.check(lambda: pytest.fail("quit"))
    assert d.shown == ["Dictate 9.9.9 is out"] and opened == []


def test_offline_says_so(ui, monkeypatch) -> None:
    d, _ = ui("OK")

    def fail():
        raise OSError("no internet")

    monkeypatch.setattr(updates, "latest_release", fail)
    updates.check(lambda: pytest.fail("quit"))
    assert d.shown == ["Couldn't check for updates"]
