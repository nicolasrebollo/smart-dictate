"""Local mode in the Windows app: the speech recognition and the AI cleanup run on this computer, exactly
like `dictate pc` from a source checkout, but with no terminal. Two variants ([client] local_device):

  gpu  an NVIDIA graphics card: the full speech model and Gemma for the cleanup (about 6 GB to download)
  cpu  the processor only, for any PC (a ThinkPad, a laptop with integrated graphics): the int8 speech
       model, llama.cpp's processor build and a small cleanup model (about 1.5 GB to download)

The installer stays small, so the first time someone picks one, the app downloads it into its own folder
(%LOCALAPPDATA%\\dictate\\app-runtime):

  1. Python 3.12, through uv (bundled in the app)
  2. the server's Python packages, pinned to the exact versions and hashes of the project's lock file
     (each variant has its own environment: venv, venv-cpu; the GPU and processor builds of ONNX
     Runtime can't share one)
  3. this version of Dictate itself (a wheel bundled in the app)
  4. the models (`dictate setup`: speech recognition, the cleanup model, llama.cpp; all hash-checked,
     shared by both variants where they're the same files)

Then the app starts `dictate server` from there in the background (no window), inside a Job Object so
it can never outlive the app, and connects to it on 127.0.0.1. Uninstalling the app deletes the folder.
Both runtime processes get DICTATE_LOCAL_DEVICE, so they use the variant they belong to (config.py).
"""

from __future__ import annotations

import contextlib
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
DEVICES = ("gpu", "cpu")
MIN_VRAM_MB = 6000  # Parakeet (~2.5 GB) + the cleanup model (~3 GB) with room to spare
Report = Callable[[str, float | None], None]  # (what's happening, fraction done or None)

# What each variant's setup says while it works.
_STEPS = {
    "gpu": ("Downloading the GPU libraries (about 3 GB)…", "Downloading the speech and AI models (about 3 GB)…"),
    "cpu": ("Downloading the speech engine (about 150 MB)…", "Downloading the speech and AI models (about 1.3 GB)…"),
}
_REQUIREMENTS = {"gpu": "requirements.txt", "cpu": "requirements-cpu.txt"}


# -- where things are ------------------------------------------------------------------------------
def runtime_dir() -> Path:
    return paths.data_dir() / "app-runtime"


def models_dir() -> Path:
    return runtime_dir() / "models"


def _venv(device: str = "gpu") -> Path:
    return runtime_dir() / ("venv" if device == "gpu" else f"venv-{device}")


def _python(device: str = "gpu", windowless: bool = False) -> Path:
    return _venv(device) / "Scripts" / ("pythonw.exe" if windowless else "python.exe")


def bundle_dir() -> Path | None:
    """uv, the Dictate wheel and the pinned requirements, shipped inside the Windows app."""
    base = getattr(sys, "_MEIPASS", None)
    if base is None:
        return None
    path = Path(base) / "runtime"
    return path if (path / "uv.exe").is_file() else None


def _marker(device: str = "gpu") -> Path:
    return runtime_dir() / ("installed.json" if device == "gpu" else f"installed-{device}.json")


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
    """The graphics card, if the GPU variant can be offered here (Windows app with an NVIDIA card)."""
    if bundle_dir() is None:
        return None
    return nvidia_gpu()


def cpu_available() -> bool:
    """The processor variant works on any Windows PC the app runs on (64-bit Windows 10/11)."""
    bundle = bundle_dir()
    return bundle is not None and (bundle / _REQUIREMENTS["cpu"]).is_file()


def installed(device: str = "gpu") -> bool:
    try:
        data = json.loads(_marker(device).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return data.get("version") == __version__ and _python(device).is_file()


def ready(cfg) -> bool:
    """Set up for this version of the app, with every model the settings call for (someone may have
    picked another cleanup model in the settings since: then the setup runs again and fetches it)."""
    import copy

    from dictate import models

    device = cfg.client.local_device
    if not installed(device):
        return False
    c = copy.deepcopy(cfg)
    c.models_dir = models_dir()
    missing = models.missing_models(c)
    if missing:
        log.info("local mode (%s) is missing %s: setting up again", device, ", ".join(missing))
    return not missing


# -- install / update ---------------------------------------------------------------------------------
def _env(device: str = "gpu") -> dict[str, str]:
    rt = runtime_dir()
    return {
        **os.environ,
        "UV_PYTHON_INSTALL_DIR": str(rt / "python"),
        "UV_CACHE_DIR": str(rt / "cache"),
        "UV_PYTHON_PREFERENCE": "only-managed",  # never pick up a Python the person installed themselves
        "UV_NO_CONFIG": "1",
        "DICTATE_MODELS_DIR": str(models_dir()),
        "DICTATE_LOCAL_DEVICE": device,  # the setup and the server use this variant's models and engine
        "PYTHONUTF8": "1",
        "DICTATE_QUIET": "1",  # the server doesn't print its link (with the token) into local-server.log
        "PYTHONUNBUFFERED": "1",
        "HF_XET_HIGH_PERFORMANCE": "1",  # the models download at full speed (see models.FAST_DOWNLOADS)
        "HF_XET_TELEMETRY_ENABLED": "0",
    }


def _run(args: list[str], log_file, report: Report, what: str, start: float, end: float, device: str = "gpu") -> None:
    """Run one step, streaming its output to the log and its latest line to the progress window."""
    log_file.write(f"\n$ {' '.join(args)}\n")
    log_file.flush()
    proc = subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=_env(device), text=True, encoding="utf-8",
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


def _downloaded_python() -> Path | None:
    """python.exe of the CPython 3.12 that uv downloaded into the runtime: the real folder
    (cpython-3.12.16-windows-…), not uv's shortcut to it (cpython-3.12-windows-…)."""
    found = []
    for d in (runtime_dir() / "python").glob(f"cpython-{PYTHON}.*-windows-*"):
        if d.is_dir() and not d.is_junction() and not d.is_symlink() and (d / "python.exe").is_file():
            patch = d.name.split("-")[1].split(".")[-1]
            found.append((int(patch) if patch.isdigit() else 0, d / "python.exe"))
    return max(found)[1] if found else None


def _make_venv(uv: str, log_file, report: Report, device: str = "gpu") -> None:
    """The runtime's Python environment. Normally uv makes it in one go. On some PCs Windows refuses the
    shortcut (a junction) uv makes next to the Python it downloads ("untrusted mount point", os error 448,
    e.g. with folder redirection or strict security policies). The download itself is complete by then, so
    fall back to Python's own venv module, which needs no shortcut."""
    venv = _venv(device)
    what = "Getting Python…"
    try:
        _run([uv, "venv", "--python", PYTHON, str(venv)], log_file, report, what, 0.02, 0.06, device)
        return
    except RuntimeError as e:
        first = e
    log_file.write("\nuv couldn't set up the environment; using Python's own venv module instead\n")
    if _downloaded_python() is None:
        with contextlib.suppress(RuntimeError):  # the same shortcut can fail here too, after the files are in place
            _run([uv, "python", "install", PYTHON], log_file, report, what, 0.02, 0.05, device)
    python = _downloaded_python()
    if python is None:
        raise first
    shutil.rmtree(venv, ignore_errors=True)  # whatever uv left half-made
    _run([str(python), "-m", "venv", str(venv)], log_file, report, what, 0.02, 0.06, device)


def install(report: Report, device: str = "gpu") -> None:
    """Download and set up a variant (or update it after the app was updated). Blocks for a while."""
    if device not in DEVICES:
        raise ValueError(f"unknown local variant {device!r}")
    bundle = bundle_dir()
    if bundle is None:
        raise RuntimeError("This copy of Dictate can't set up local mode (it isn't the Windows app).")
    requirements = bundle / _REQUIREMENTS[device]
    if not requirements.is_file():
        raise RuntimeError(f"This copy of Dictate is missing {requirements.name}; download it again.")
    rt = runtime_dir()
    rt.mkdir(parents=True, exist_ok=True)
    _marker(device).unlink(missing_ok=True)
    uv = str(bundle / "uv.exe")
    wheel = next(bundle.glob("dictate-*.whl"))
    python = str(_python(device))
    libraries, models = _STEPS[device]
    log_dir = paths.log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    with (log_dir / "local-setup.log").open("a", encoding="utf-8") as f:
        f.write(f"\n=== setting up local mode ({device}), Dictate {__version__} ===\n")
        report("Getting Python…", 0.02)
        if not _python(device).is_file():
            _make_venv(uv, f, report, device)
        _run(
            [uv, "pip", "install", "--python", python, "--require-hashes", "-r", str(requirements)],
            f, report, libraries, 0.06, 0.45 if device == "gpu" else 0.15, device,
        )  # fmt: skip
        _run(
            [uv, "pip", "install", "--python", python, "--no-deps", "--reinstall-package", "dictate", str(wheel)],
            f, report, "Installing Dictate's speech engine…", 0.45, 0.5 if device == "gpu" else 0.2, device,
        )  # fmt: skip
        _run([python, "-m", "dictate.cli", "setup"], f, report, models, 0.5, 0.98, device)
    _marker(device).write_text(json.dumps({"version": __version__, "device": device}), encoding="utf-8")
    report("Done", 1.0)


def remove() -> None:
    """Delete the runtime and its models (frees several GB)."""
    shutil.rmtree(runtime_dir(), ignore_errors=True)


# -- run ----------------------------------------------------------------------------------------------
class LocalServer:
    """`dictate server` from the runtime, in the background, tied to this app's lifetime."""

    def __init__(self, port: int, device: str = "gpu") -> None:
        from dictate.llm.llama_server import _Job

        log_dir = paths.log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        self._log = (log_dir / "local-server.log").open("a", encoding="utf-8")
        self.proc = subprocess.Popen(
            [str(_python(device, windowless=True)), "-m", "dictate.cli", "server"],
            stdout=self._log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=_env(device),
            creationflags=subprocess.CREATE_NO_WINDOW,
        )  # fmt: skip
        self._job = _Job()  # kills the server (and its llama-server) if the app exits or crashes
        self._job.assign(self.proc.pid)
        self.port = port
        self.device = device
        log.info("local server started (%s, pid %d, port %d)", device, self.proc.pid, port)

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
