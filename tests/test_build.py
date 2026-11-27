"""The Mac build's fix for binaries that link Apple's Swift libraries (they live in macOS, not the app).
Runs anywhere: otool and install_name_tool are stand-ins here, answering as they do on a Mac."""

import importlib.util
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_script", ROOT / "packaging" / "build.py")
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


def _otool_L(path: str, libs: list[str]) -> str:
    return f"{path}:\n" + "".join(f"\t{lib} (compatibility version 1.0.0, current version 1.0.0)\n" for lib in libs)


def _otool_l(rpaths: list[str]) -> str:
    out = "Load command 0\n      cmd LC_SEGMENT_64\n  cmdsize 72\n  segname __PAGEZERO\n"
    for i, rp in enumerate(rpaths, 1):
        out += f"Load command {i}\n          cmd LC_RPATH\n      cmdsize 32\n         path {rp} (offset 12)\n"
    return out


SYSTEM = ["/System/Library/Frameworks/CoreMedia.framework/Versions/A/CoreMedia", "/usr/lib/libSystem.B.dylib"]
SWIFT = "@rpath/libswiftCoreMedia.dylib"


def test_parsing_otool() -> None:
    assert build._linked_libs(_otool_L("/x.so", [SWIFT, *SYSTEM])) == [SWIFT, *SYSTEM]
    assert build._rpaths(_otool_l(["@loader_path/..", "/usr/lib/swift"])) == ["@loader_path/..", "/usr/lib/swift"]
    assert build._rpaths(_otool_l([])) == []


def test_swift_libraries_are_found_in_macos(tmp_path) -> None:
    fw = tmp_path / "Dictate.app" / "Contents" / "Frameworks"
    (fw / "CoreMedia").mkdir(parents=True)
    files = {  # name -> (linked libraries, search paths), as PyInstaller leaves them
        "CoreMedia/_CoreMedia.cpython-312-darwin.so": ([SWIFT, *SYSTEM], ["@loader_path/.."]),
        "CoreMedia/_inlines.cpython-312-darwin.so": ([SWIFT, *SYSTEM], ["@loader_path/..", "@loader_path"]),
        "mixed.so": ([SWIFT, "@rpath/libbundled.dylib", *SYSTEM], ["@loader_path"]),
        "done.so": ([SWIFT], ["/usr/lib/swift"]),
        "_ssl.cpython-312-darwin.so": (["@rpath/libssl.3.dylib", *SYSTEM], ["@loader_path"]),
    }
    for name in [*files, "base_library.zip"]:
        (fw / name).write_bytes(b"")
    changes: dict[str, list[str]] = {}

    def run(args, **kw):
        path = args[-1]
        name = Path(path).relative_to(fw).as_posix()
        out = ""
        if args[:2] == ["otool", "-L"]:
            out = _otool_L(path, files[name][0])
        elif args[:2] == ["otool", "-l"]:
            out = _otool_l(files[name][1])
        elif args[0] == "install_name_tool":
            changes[name] = args[1:-1]
        return subprocess.CompletedProcess(args, 0, out, "")

    fixed = build.swift_rpath_fixes(tmp_path / "Dictate.app", run=run)
    assert {p.relative_to(fw).as_posix() for p in fixed} == set(changes)
    # its only search path served the Swift library: swap it (no room in the header to add one)
    assert changes["CoreMedia/_CoreMedia.cpython-312-darwin.so"] == ["-rpath", "@loader_path/..", "/usr/lib/swift"]
    assert changes["CoreMedia/_inlines.cpython-312-darwin.so"] == [
        "-rpath", "@loader_path/..", "/usr/lib/swift", "-delete_rpath", "@loader_path",
    ]  # fmt: skip
    # it also finds a library in the app that way: keep that, add the system's Swift
    assert changes["mixed.so"] == ["-add_rpath", "/usr/lib/swift"]
    assert "done.so" not in changes and "_ssl.cpython-312-darwin.so" not in changes
