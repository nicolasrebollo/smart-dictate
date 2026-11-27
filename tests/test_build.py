"""The Mac build's fix for binaries that link Apple's Swift libraries (they live in macOS, not the app).
Runs anywhere: otool and install_name_tool are stand-ins here."""

import importlib.util
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_script", ROOT / "packaging" / "build.py")
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


def test_only_binaries_that_need_swift_get_its_search_path(tmp_path) -> None:
    frameworks = tmp_path / "Dictate.app" / "Contents" / "Frameworks"
    (frameworks / "CoreMedia").mkdir(parents=True)
    needs = frameworks / "CoreMedia" / "_CoreMedia.cpython-312-darwin.so"
    has_it = frameworks / "CoreMedia" / "_inlines.cpython-312-darwin.so"
    plain = frameworks / "_ssl.cpython-312-darwin.so"
    for f in (needs, has_it, plain, frameworks / "base_library.zip"):
        f.write_bytes(b"")
    calls = []

    def run(args, **kw):
        calls.append(args)
        path = args[-1]
        out = ""
        if args[:2] == ["otool", "-L"]:
            out = "@rpath/libswiftCoreMedia.dylib (compatibility 1.0.0)" if "CoreMedia" in path else "libSystem"
        elif args[:2] == ["otool", "-l"] and "_inlines" in path:
            out = "cmd LC_RPATH\n path /usr/lib/swift (offset 12)"
        return subprocess.CompletedProcess(args, 0, out, "")

    fixed = build.swift_rpath_fixes(tmp_path / "Dictate.app", run=run)
    assert fixed == [needs]
    assert ["install_name_tool", "-add_rpath", "/usr/lib/swift", str(needs)] in calls
    assert not any(c[0] == "install_name_tool" and c[-1] != str(needs) for c in calls)
    assert not any(c[-1].endswith(".zip") for c in calls)
