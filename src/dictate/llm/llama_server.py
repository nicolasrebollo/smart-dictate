"""llama.cpp's llama-server, run as a managed child process bound to 127.0.0.1.

Why a server instead of embedding: the official prebuilt Windows CUDA builds track new model
architectures within days and need no compiler, and the server's prompt cache reuses the
static prefix (system prompt + examples) across calls. Loopback HTTP adds well under 1 ms.

The child is placed in a Windows Job Object with KILL_ON_JOB_CLOSE, so it dies with dictate
even if dictate crashes. It never touches the network beyond its loopback listener.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from dictate.llm.base import Completion, LLMError, LLMTimeout, Message

log = logging.getLogger(__name__)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _cuda_dll_dirs() -> list[str]:
    """CUDA runtime/cuBLAS DLLs shipped by the nvidia-* pip wheels (shared with ONNX Runtime)."""
    try:
        import nvidia

        roots = [Path(p) for p in nvidia.__path__]
    except ImportError:
        return []
    dirs = set()
    for root in roots:
        for dll in root.rglob("cublas64_*.dll"):
            dirs.add(str(dll.parent))
        for dll in root.rglob("cudart64_*.dll"):
            dirs.add(str(dll.parent))
    return sorted(dirs)


class _Job:
    """Windows Job Object that kills its processes when the last handle closes."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]

        class BASIC(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IO(ctypes.Structure):
            _fields_ = [
                (n, ctypes.c_uint64)
                for n in (
                    "ReadOperationCount",
                    "WriteOperationCount",
                    "OtherOperationCount",
                    "ReadTransferCount",
                    "WriteTransferCount",
                    "OtherTransferCount",
                )
            ]

        class EXTENDED(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BASIC),
                ("IoInfo", IO),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        self._k32 = k32
        self.handle = k32.CreateJobObjectW(None, None)
        info = EXTENDED()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        k32.SetInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info))

    def assign(self, pid: int) -> None:
        proc = self._k32.OpenProcess(0x0001 | 0x0100, False, pid)  # TERMINATE | SET_QUOTA
        if proc:
            self._k32.AssignProcessToJobObject(self.handle, proc)


class LlamaServer:
    def __init__(
        self,
        exe: Path,
        model: Path,
        *,
        context: int = 4096,
        gpu_layers: int = 99,
        port: int = 0,
        log_path: Path | None = None,
        extra_args: Sequence[str] = (),
    ) -> None:
        if not exe.is_file():
            raise LLMError(f"llama-server not found at {exe} - run `dictate setup`")
        if not model.is_file():
            raise LLMError(f"LLM model not found at {model} - run `dictate setup`")
        self.name = f"llama-server:{model.stem}"
        self.port = port or _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        args = [
            str(exe),
            "-m",
            str(model),
            "--host",
            "127.0.0.1",
            "--port",
            str(self.port),
            "-c",
            str(context),
            "-ngl",
            str(gpu_layers),
            "-np",
            "1",
            "-fa",
            "on",
            "--no-webui",
            "--reasoning",
            "off",
            "--cache-reuse",
            "256",
            "--ctx-checkpoints",
            "4",
            "--swa-full",
            "-b",
            "2048",
            "-ub",
            "512",
            *extra_args,
        ]
        env = os.environ.copy()
        env["PATH"] = os.pathsep.join([*_cuda_dll_dirs(), str(exe.parent), env.get("PATH", "")])
        env["LLAMA_ARG_NO_WEBUI"] = "1"
        out = open(log_path, "ab") if log_path else subprocess.DEVNULL  # noqa: SIM115
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        log.debug("starting %s", " ".join(args))
        self._proc = subprocess.Popen(args, stdout=out, stderr=subprocess.STDOUT, env=env, creationflags=flags)
        self._job = _Job() if sys.platform == "win32" else None
        if self._job:
            self._job.assign(self._proc.pid)
        self._wait_ready(timeout_s=120)
        self._template_cache: dict[str, str] = {}

    # -- HTTP -----------------------------------------------------------------------------
    def _post(self, path: str, payload: dict[str, Any], timeout_s: float) -> dict[str, Any]:
        req = urllib.request.Request(
            self.base + path, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as r:
                return json.loads(r.read())
        except TimeoutError as e:
            raise LLMTimeout(f"llama-server did not answer within {timeout_s:.1f}s") from e
        except urllib.error.URLError as e:
            if isinstance(e.reason, TimeoutError | socket.timeout):
                raise LLMTimeout(f"llama-server did not answer within {timeout_s:.1f}s") from e
            raise LLMError(f"llama-server request failed: {e}") from e

    def _wait_ready(self, timeout_s: float) -> None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                raise LLMError(f"llama-server exited with code {self._proc.returncode} (see the log file)")
            try:
                with urllib.request.urlopen(self.base + "/health", timeout=1) as r:
                    if r.status == 200:
                        return
            except (urllib.error.URLError, ConnectionError, TimeoutError):
                pass
            time.sleep(0.1)
        raise LLMError("llama-server did not become ready in time")

    # -- prompting ------------------------------------------------------------------------
    def render(self, messages: Sequence[Message]) -> str:
        """Render with the model's own chat template (+ generation prompt), via the server."""
        payload = {
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "chat_template_kwargs": {"enable_thinking": False},
        }
        key = json.dumps(payload, sort_keys=True)
        cached = self._template_cache.get(key)
        if cached is not None:
            return cached
        prompt = str(self._post("/apply-template", payload, timeout_s=5)["prompt"])
        if len(self._template_cache) > 64:
            self._template_cache.clear()
        self._template_cache[key] = prompt
        return prompt

    def complete(self, prompt: str, *, max_tokens: int, stop: Sequence[str] = (), timeout_s: float = 2.5) -> Completion:
        t0 = time.perf_counter()
        r = self._post(
            "/completion",
            {
                "prompt": prompt,
                "n_predict": max_tokens,
                "temperature": 0.0,
                "top_k": 1,
                "stop": list(stop),
                "cache_prompt": True,
                "stream": False,
                "return_tokens": False,
            },
            timeout_s=timeout_s,
        )
        timings = r.get("timings", {})
        return Completion(
            text=str(r.get("content", "")),
            prompt_tokens=int(r.get("tokens_evaluated", 0) or 0),
            prompt_evaluated=int(timings.get("prompt_n", 0) or 0),
            generated_tokens=int(timings.get("predicted_n", 0) or 0),
            prompt_ms=float(timings.get("prompt_ms", 0.0) or 0.0),
            generate_ms=float(timings.get("predicted_ms", 0.0) or 0.0),
            total_ms=(time.perf_counter() - t0) * 1000,
            truncated=bool(r.get("stopped_limit")) or r.get("stop_type") == "limit",
        )

    def chat(
        self,
        messages: Sequence[Message],
        *,
        prefill: str = "",
        max_tokens: int = 256,
        stop: Sequence[str] = (),
        timeout_s: float = 2.5,
    ) -> Completion:
        t0 = time.perf_counter()
        prompt = self.render(messages) + prefill
        c = self.complete(prompt, max_tokens=max_tokens, stop=stop, timeout_s=timeout_s)
        c.total_ms = (time.perf_counter() - t0) * 1000
        return c

    def close(self) -> None:
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()
