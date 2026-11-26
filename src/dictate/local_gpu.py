"""Local mode in the Windows app: the speech recognition and the AI cleanup run on this computer's
NVIDIA graphics card, exactly like `dictate pc` from a source checkout, but with no terminal.

The installer stays small (the GPU libraries and the models are several GB), so the first time
someone picks this mode the app downloads them into its own folder (%LOCALAPPDATA%\\dictate\\app-runtime):

  1. Python 3.12, through uv (bundled in the app)
  2. the server's Python packages, pinned to the exact versions and hashes of the project's lock file
  3. this version of Dictate itself (a wheel bundled in the app)
  4. the models (`dictate setup`: speech recognition, the cleanup model, llama.cpp; all hash-checked)

Then the app starts `dictate server` from there in the background (no window), inside a Job Object so
it can never outlive the app, and connects to it on 127.0.0.1. Uninstalling the app deletes the folder.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from dictate import __version__, paths

log = logging.getLogger(__name__)

PYTHON = "3.12"
MIN_VRAM_MB = 6000  # Parakeet (~2.5 GB) + the cleanup model (~3 GB) with room to spare
Report = Callable[[str, float | None], None]  # (what's happening, fraction done or None)


# -- where things are ------------------------------------------------------------------------------
def runtime_dir() -> Path:
    return paths.data_dir() / "app-runtime"


def models_dir() -> Path:
    return runtime_dir() / "models"


def _python(windowless: bool = False) -> Path:
    return runtime_dir() / "venv" / "Scripts" / ("pythonw.exe" if windowless else "python.exe")


def bundle_dir() -> Path | None:
    """uv, the Dictate wheel and the pinned requirements, shipped inside the Windows app."""
    base = getattr(sys, "_MEIPASS", None)
    if base is None:
        return None
    path = Path(base) / "runtime"
    return path if (path / "uv.exe").is_file() else None


def _marker() -> Path:
    return runtime_dir() / "installed.json"


# -- can this computer do it? -------------------------------------------------------------------------
def nvidia_gpu() -> tuple[str, int] | None:
    """(name, memory in MB) of the first NVIDIA graphics card, or None."""
    if sys.platform != "win32":
        return None
    exe = shutil.which("nvidia-smi") or str(
        Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32" / "nvidia-smi.exe"
    )
    try:
        out = subprocess.run(
            [exe, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW,
        ).stdout  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        name, _, mem = line.rpartition(",")
        try:
            return name.strip(), int(float(mem.strip()))
        except ValueError:
            continue
    return None


def available() -> tuple[str, int] | None:
    """The graphics card, if local mode can be offered here (Windows app with an NVIDIA card)."""
    if bundle_dir() is None:
        return None
    return nvidia_gpu()


def installed() -> bool:
    try:
        data = json.loads(_marker().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return data.get("version") == __version__ and _python().is_file()


# -- install / update ---------------------------------------------------------------------------------
def _env() -> dict[str, str]:
    rt = runtime_dir()
    return {
        **os.environ,
        "UV_PYTHON_INSTALL_DIR": str(rt / "python"),
        "UV_CACHE_DIR": str(rt / "cache"),
        "UV_PYTHON_PREFERENCE": "only-managed",  # never pick up a Python the person installed themselves
        "UV_NO_CONFIG": "1",
        "DICTATE_MODELS_DIR": str(models_dir()),
        "PYTHONUTF8": "1",
        "PYTHONUNBUFFERED": "1",
    }


def _run(args: list[str], log_file, report: Report, what: str, start: float, end: float) -> None:
    """Run one step, streaming its output to the log and its latest line to the progress window."""
    log_file.write(f"\n$ {' '.join(args)}\n")
    log_file.flush()
    proc = subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=_env(), text=True, encoding="utf-8",
        errors="replace", creationflags=subprocess.CREATE_NO_WINDOW, bufsize=1,
    )  # fmt: skip
    assert proc.stdout is not None
    tail: list[str] = []
    for line in proc.stdout:
        log_file.write(line)
        line = line.strip()
        if line:
            tail = [*tail[-9:], line]
            report(f"{what}\n\n{line[:110]}", None)
    if proc.wait() != 0:
        log_file.flush()
        raise RuntimeError(f"{what} failed:\n" + "\n".join(tail[-5:]))
    report(what, end)


def install(report: Report) -> None:
    """Download and set up everything (or update it after the app was updated). Blocks for a while."""
    bundle = bundle_dir()
    if bundle is None:
        raise RuntimeError("This copy of Dictate can't set up local mode (it isn't the Windows app).")
    rt = runtime_dir()
    rt.mkdir(parents=True, exist_ok=True)
    _marker().unlink(missing_ok=True)
    uv = str(bundle / "uv.exe")
    wheel = next(bundle.glob("dictate-*.whl"))
    log_dir = paths.log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    with (log_dir / "local-setup.log").open("a", encoding="utf-8") as f:
        report("Getting Python…", 0.02)
        if not _python().is_file():
            _run([uv, "venv", "--python", PYTHON, str(rt / "venv")], f, report, "Getting Python…", 0.02, 0.06)
        _run(
            [uv, "pip", "install", "--python", str(_python()), "--require-hashes",
             "-r", str(bundle / "requirements.txt")],
            f, report, "Downloading the GPU libraries (about 3 GB)…", 0.06, 0.45,
        )  # fmt: skip
        _run(
            [uv, "pip", "install", "--python", str(_python()), "--no-deps",
             "--reinstall-package", "dictate", str(wheel)],
            f, report, "Installing Dictate's speech engine…", 0.45, 0.5,
        )  # fmt: skip
        _run(
            [str(_python()), "-m", "dictate.cli", "setup"], f, report,
            "Downloading the speech and AI models (about 3 GB)…", 0.5, 0.98,
        )  # fmt: skip
    _marker().write_text(json.dumps({"version": __version__}), encoding="utf-8")
    report("Done", 1.0)


def remove() -> None:
    """Delete the runtime and its models (frees several GB)."""
    shutil.rmtree(runtime_dir(), ignore_errors=True)


# -- run ----------------------------------------------------------------------------------------------
class LocalServer:
    """`dictate server` from the runtime, in the background, tied to this app's lifetime."""

    def __init__(self, port: int) -> None:
        from dictate.llm.llama_server import _Job

        log_dir = paths.log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        self._log = (log_dir / "local-server.log").open("a", encoding="utf-8")
        self.proc = subprocess.Popen(
            [str(_python(windowless=True)), "-m", "dictate.cli", "server"],
            stdout=self._log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=_env(),
            creationflags=subprocess.CREATE_NO_WINDOW,
        )  # fmt: skip
        self._job = _Job()  # kills the server (and its llama-server) if the app exits or crashes
        self._job.assign(self.proc.pid)
        self.port = port
        log.info("local server started (pid %d, port %d)", self.proc.pid, port)

    @property
    def running(self) -> bool:
        return self.proc.poll() is None

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self._log.close()
