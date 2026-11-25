"""Guided recording of benchmark clips: `dictate bench record`.

Shows each prompt, records between two Enter presses, saves 16 kHz mono WAV + a JSON sidecar
with the reference text. Clips stay local (bench/asr/clips is git-ignored).
"""

from __future__ import annotations

import datetime as dt
import json
import threading

import numpy as np
import sounddevice as sd
import soundfile as sf

from dictate.audio import level_report, resolve_device
from dictate.bench import CLIPS_DIR, load_prompts
from dictate.vad import SAMPLE_RATE


class _Capture:
    def __init__(self, device: int | None) -> None:
        self._chunks: list[np.ndarray] = []
        self._lock = threading.Lock()
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            blocksize=512,
            device=device,
            callback=self._cb,
        )

    def _cb(self, indata, frames, time_info, status) -> None:
        with self._lock:
            self._chunks.append(indata[:, 0].copy())

    def record_until_enter(self) -> np.ndarray:
        with self._lock:
            self._chunks = []
        self._stream.start()
        try:
            input("  ● recording... press Enter to stop ")
        finally:
            self._stream.stop()
        with self._lock:
            return np.concatenate(self._chunks) if self._chunks else np.zeros(0, np.float32)


def record_session(*, only: list[str] | None = None, redo: bool = False, device: str = "") -> None:
    prompts = load_prompts()
    if only:
        prompts = [p for p in prompts if p.id in only or p.lang in only]
    CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    dev = resolve_device(device)
    dev_name = sd.query_devices(dev if dev is not None else sd.default.device[0])["name"]
    todo = [p for p in prompts if redo or not (CLIPS_DIR / f"{p.id}.wav").exists()]

    print(f"\nRecording {len(todo)} of {len(prompts)} clips into {CLIPS_DIR}")
    print(f"Microphone: {dev_name}")
    print("Use the mic and room you normally dictate in. Read each text in your natural voice;")
    print("the 'style' line says how (normal, fast, with pauses...). Leave ~0.5 s before speaking.")
    print("Keys: Enter = start/stop, r = redo, p = play back, s = skip, q = quit.\n")
    if not todo:
        print("All clips already recorded. Use --redo to record again.")
        return

    cap = _Capture(dev)
    for i, p in enumerate(todo, 1):
        while True:
            print(f"[{i}/{len(todo)}] {p.id}  ({p.lang}, {p.category}; style: {p.style})")
            print(f'    "{p.text}"')
            cmd = input("  Enter = record, s = skip, q = quit > ").strip().lower()
            if cmd == "q":
                print("Stopped. Run the same command again to continue where you left off.")
                return
            if cmd == "s":
                break
            audio = cap.record_until_enter()
            lv = level_report(audio)
            dur = len(audio) / SAMPLE_RATE
            peak_db = 20 * np.log10(max(lv.peak, 1e-9))
            print(f"  {dur:.1f}s, peak {peak_db:.0f} dBFS, level {lv.rms_dbfs:.0f} dBFS")
            if warning := lv.warning():
                print(f"  ! {warning}")
            if dur < 0.5:
                print("  ! too short - try again")
                continue
            choice = ""
            while True:
                choice = input("  Enter = save & next, p = play back, r = redo > ").strip().lower()
                if choice == "p":
                    sd.play(audio, SAMPLE_RATE)
                    sd.wait()
                    continue
                break
            if choice == "r":
                continue
            sf.write(CLIPS_DIR / f"{p.id}.wav", audio, SAMPLE_RATE, subtype="PCM_16")
            meta = {
                "id": p.id,
                "lang": p.lang,
                "category": p.category,
                "ref": p.text,
                "duration_s": round(dur, 2),
                "device": dev_name,
                "recorded_at": dt.datetime.now().isoformat(timespec="seconds"),
            }
            (CLIPS_DIR / f"{p.id}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            print('  saved. (If you misread a word, fix "ref" in the .json file.)\n')
            break
    print("Done. Run `uv run dictate bench asr` to measure accuracy and latency.")
