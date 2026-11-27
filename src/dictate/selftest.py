"""`Dictate --self-test=REPORT.json`: checks that a built app has everything online mode needs, without
the hotkey, the tray, the microphone or any network: every module imports, the bundled speech detector
loads and runs, certificates can be checked against the system's list, and a whole (silent) dictation goes
through the online-mode engine and its WebSocket. The GitHub workflow runs it on every build.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path


def _checks() -> dict[str, str]:
    results: dict[str, str] = {}

    def check(name: str, fn) -> None:
        try:
            results[name] = "ok" + (f": {r}" if (r := fn()) else "")
        except BaseException:
            results[name] = "FAILED: " + traceback.format_exc(limit=3).strip().splitlines()[-1]

    def imports() -> str:
        import anthropic
        import keyring
        import numpy
        import onnxruntime

        import dictate.cloud.chat
        import dictate.cloud.claude
        import dictate.cloud.speech
        import dictate.engine
        import dictate.server  # noqa: F401

        backend = type(keyring.get_keyring()).__name__
        return f"onnxruntime {onnxruntime.__version__}, numpy {numpy.__version__}, " \
            f"anthropic {anthropic.__version__}, keyring {backend}"  # fmt: skip

    def vad() -> str:
        import numpy as np

        from dictate import models
        from dictate.config import Config
        from dictate.vad import FRAME, SileroVAD

        path = models.silero_path(Config())
        if not path.is_file():
            models.download_silero(Config())  # a source checkout (the app carries its own copy)
        v = SileroVAD(path)
        probs = v.feed(np.zeros(FRAME * 4, dtype=np.float32))
        return f"{path.name}, {len(probs)} frames"

    def tls() -> str:
        from dictate.client import tls_context

        return type(tls_context("wss://x")).__module__

    def local_bundle() -> str:
        if sys.platform != "win32" or not getattr(sys, "frozen", False):
            return "skipped (only in the Windows app)"
        from dictate import local_gpu

        b = local_gpu.bundle_dir()
        if b is None:
            raise RuntimeError("local-mode bundle missing")
        return ", ".join(sorted(p.name for p in b.iterdir()))

    def notes() -> str:
        import tkinter

        import dictate.notes.window  # noqa: F401
        from dictate.notes.store import NoteStore

        store = NoteStore()  # in the self-test's own data folder
        try:
            folder = store.add_folder("School")
            store.add_note(folder, "Lecture", "Café at 3")
            found = store.search("cafe", folder_id=folder)
            if [n.title for n in found] != ["Lecture"]:
                raise RuntimeError(f"search found {found}")
        finally:
            store.close()
        tcl = tkinter.Tcl().eval("info patchlevel")  # the window's toolkit is in the app
        return f"sqlite {__import__('sqlite3').sqlite_version}, Tcl {tcl}, Tk {tkinter.TkVersion}"

    def dictation() -> str:
        from websockets.sync.client import connect

        from dictate import modes
        from dictate.config import parse_config

        os.environ.setdefault("DICTATE_OPENAI_KEY", "sk-selftest")
        os.environ.setdefault("DICTATE_ANTHROPIC_KEY", "sk-ant-selftest")
        cfg = parse_config('[client]\nmode = "online"\n[online]\nspeech = "openai"\ncleanup = "anthropic"\n'
                           'speech_model = "gpt-transcribe"\ncleanup_model = "claude-opus-5-5"\n')  # fmt: skip
        session = modes.start(cfg)
        try:
            with connect(f"{session.server}/ws?token={session.token}") as ws:
                ws.send(json.dumps({"type": "start", "language": "en"}))
                ws.send(b"\0\0" * 16000)  # 1 s of silence: nothing to send to the online service
                ws.send(json.dumps({"type": "stop", "raw": False}))
                reply = json.loads(ws.recv(timeout=30))
        finally:
            session.close()
        if reply.get("type") != "result" or reply.get("mode") != "empty":
            raise RuntimeError(f"unexpected reply {reply}")
        return session.label

    check("imports", imports)
    check("speech detection", vad)
    check("certificates", tls)
    check("local mode bundle", local_bundle)
    check("notes", notes)
    check("online dictation", dictation)
    return results


def run(report: str) -> int:
    tmp = tempfile.mkdtemp(prefix="dictate-selftest-")
    os.environ["DICTATE_CONFIG_DIR"] = tmp  # never touch the real settings
    os.environ["DICTATE_DATA_DIR"] = tmp
    try:
        results = _checks()
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)
    ok = all(v.startswith("ok") for v in results.values())
    text = json.dumps({"ok": ok, "frozen": bool(getattr(sys, "frozen", False)), "checks": results}, indent=2)
    if report and report != "-":
        Path(report).write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0 if ok else 1
