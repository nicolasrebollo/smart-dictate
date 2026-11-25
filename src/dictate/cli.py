"""Command line entry point: `dictate [run|setup|devices|config|lang|transcribe|bench]`."""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from pathlib import Path

from dictate import offline, paths
from dictate.config import LANGUAGES, ConfigError, ensure_config_file, load_config
from dictate.logs import setup_logging

log = logging.getLogger("dictate")


def _mic_access_denied() -> bool:
    """Windows privacy switch 'Let desktop apps access your microphone'."""
    if sys.platform != "win32":
        return False
    import winreg

    base = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"
    for sub in ("", r"\NonPackaged"):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, base + sub) as k:
                if str(winreg.QueryValueEx(k, "Value")[0]).lower() == "deny":
                    return True
        except OSError:
            continue
    return False


def _load(args: argparse.Namespace):
    try:
        return load_config(Path(args.config) if args.config else None)
    except ConfigError as e:
        raise SystemExit(f"Config error: {e}") from e


def _go_offline(cfg) -> None:
    offline.enforce_offline_env()
    if cfg.privacy.strict_offline:
        offline.install_network_guard()


def cmd_run(args: argparse.Namespace) -> None:
    cfg = _load(args)
    setup_logging(args.debug or cfg.logging.debug)
    _go_offline(cfg)
    log.info("config: %s", cfg.source_path)
    if _mic_access_denied():
        log.error(
            "Windows is blocking microphone access for desktop apps. Turn on: Settings > Privacy & "
            "security > Microphone > 'Let desktop apps access your microphone'."
        )
    from dictate.app import App

    App(cfg, dry_run=getattr(args, "dry_run", False)).run()


def cmd_setup(args: argparse.Namespace) -> None:
    cfg = _load(args)
    setup_logging(args.debug)
    from dictate.models import run_setup

    run_setup(cfg, all_variants=args.all, llms=args.llm)


def cmd_models(args: argparse.Namespace) -> None:
    from dictate import models

    cfg = _load(args)
    for name, spec in models.LLM_MODELS.items():
        have = "downloaded" if models.llm_path(cfg, name).is_file() else "-"
        cur = " (current)" if name == cfg.cleanup.model else ""
        print(f"  {name:14s} {spec.size_gb:4.1f} GB  {have:10s}  {spec.note}{cur}")
    print()
    print("Set [cleanup] model in the config; download with `dictate setup --llm NAME`.")


def cmd_devices(args: argparse.Namespace) -> None:
    from dictate.audio import list_input_devices

    for idx, name, api, is_default in list_input_devices():
        print(f"{'*' if is_default else ' '} {idx:3d}  {name}  [{api}]")
    print("\n* = system default. Set [audio] input_device to a number or part of a name.")


def cmd_config(args: argparse.Namespace) -> None:
    path = ensure_config_file(Path(args.config) if args.config else None)
    print(path)
    if args.edit and sys.platform == "win32":
        os.startfile(path)


def cmd_lang(args: argparse.Namespace) -> None:
    path = ensure_config_file(Path(args.config) if args.config else None)
    text = path.read_text(encoding="utf-8")
    new, n = re.subn(r'(?m)^(\s*language\s*=\s*)"[a-z]*"', rf'\g<1>"{args.language}"', text, count=1)
    if n == 0:
        raise SystemExit(f'could not find `language = "..."` in {path}; edit it by hand')
    path.write_text(new, encoding="utf-8")
    print(f"language set to {args.language} (a running dictate picks this up on the next utterance)")


def cmd_transcribe(args: argparse.Namespace) -> None:
    cfg = _load(args)
    setup_logging(args.debug)
    _go_offline(cfg)
    import soundfile as sf

    from dictate import models
    from dictate.asr import create_asr
    from dictate.pipeline import Pipeline, vad_probs_offline
    from dictate.timing import Timings
    from dictate.vad import SAMPLE_RATE, SileroVAD

    vad = SileroVAD(models.silero_path(cfg))
    asr = create_asr(cfg)
    asr.warmup()
    pipe = Pipeline(cfg, asr)
    for f in args.files:
        audio, sr = sf.read(f, dtype="float32", always_2d=True)
        audio = audio.mean(axis=1)
        if sr != SAMPLE_RATE:
            raise SystemExit(f"{f}: expected 16 kHz audio, got {sr} Hz")
        for i in range(args.repeat):
            t = Timings()
            with t.stage("vad_probs"):
                probs = vad_probs_offline(vad, audio)
            res = pipe.process(audio, probs, timings=t)
            if i == args.repeat - 1:
                print(f"\n{f}  ({res.audio_s:.1f}s audio)")
                print(f"  asr:   {res.asr_text}")
                print(f"  final: {res.text}")
            print(f"  {t.summary()}")


def cmd_bench(args: argparse.Namespace) -> None:
    if args.bench_cmd == "record":
        from dictate.bench.record import record_session

        record_session(only=args.only, redo=args.redo, device=args.device)
    elif args.bench_cmd == "cleanup":
        cfg = _load(args)
        setup_logging(args.debug)
        _go_offline(cfg)
        from dictate.bench.cleanup import run_cleanup_bench
        from dictate.models import LLM_MODELS

        run_cleanup_bench(cfg, args.models or list(LLM_MODELS), with_clips=not args.no_clips)
    elif args.bench_cmd in ("e2e", "streaming"):
        cfg = _load(args)
        setup_logging(args.debug)
        _go_offline(cfg)
        from dictate.bench.e2e import run_e2e_bench, run_streaming_bench

        if args.bench_cmd == "e2e":
            run_e2e_bench(cfg, repeat=args.repeat)
        else:
            run_streaming_bench(cfg)
    elif args.bench_cmd == "asr":
        cfg = _load(args)
        setup_logging(args.debug)
        _go_offline(cfg)
        from dictate.bench.asr import run_asr_bench

        run_asr_bench(
            cfg,
            variants=args.variants,
            repeat=args.repeat,
            clips_dir=Path(args.clips) if args.clips else None,
        )


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="dictate", description="Local, private push-to-talk dictation.")
    p.add_argument("--config", help=f"config file (default: {paths.config_path()})")
    p.add_argument("--debug", action="store_true", help="verbose logging")
    sub = p.add_subparsers(dest="cmd")

    r = sub.add_parser("run", help="start the dictation daemon (default)")
    r.add_argument("--dry-run", action="store_true", help="print results instead of typing them (mic check)")
    s = sub.add_parser("setup", help="download models (the only command that uses the network)")
    s.add_argument("--all", action="store_true", help="also fetch the alternative (fp32 + int8) ASR variants")
    s.add_argument("--llm", nargs="*", default=[], help="extra cleanup models to download (see `dictate models`)")
    sub.add_parser("models", help="list cleanup models and which are downloaded")
    sub.add_parser("devices", help="list microphones")
    c = sub.add_parser("config", help="print the config path")
    c.add_argument("--edit", action="store_true", help="open it in your editor")
    lg = sub.add_parser("lang", help="switch dictation language")
    lg.add_argument("language", choices=LANGUAGES)
    t = sub.add_parser("transcribe", help="run the pipeline on WAV files (16 kHz) and print timings")
    t.add_argument("files", nargs="+")
    t.add_argument("--repeat", type=int, default=3)

    b = sub.add_parser("bench", help="benchmarks")
    bsub = b.add_subparsers(dest="bench_cmd", required=True)
    br = bsub.add_parser("record", help="record your voice reading the benchmark prompts")
    br.add_argument("--only", nargs="*", help="prompt ids or languages (en/es/fr) to record")
    br.add_argument("--redo", action="store_true", help="re-record clips that already exist")
    br.add_argument("--device", default="", help="input device (index or name substring)")
    ba = bsub.add_parser("asr", help="WER + latency over recorded clips")
    ba.add_argument(
        "--variants",
        nargs="*",
        default=["cuda", "cpu-int8"],
        help="ASR variants to compare: cuda, cuda-int8, cpu, cpu-int8",
    )
    ba.add_argument("--repeat", type=int, default=3)
    ba.add_argument("--clips", help="clips directory (default: bench/asr/clips)")
    be = bsub.add_parser("e2e", help="release-to-text latency per stage, live preview on vs off")
    be.add_argument("--repeat", type=int, default=2)
    bsub.add_parser("streaming", help="live preview lag, flicker, and streamed vs whole-utterance accuracy")
    bc = bsub.add_parser("cleanup", help="compare cleanup models on the eval set + your clips")
    bc.add_argument("--models", nargs="*", help="model names (default: all downloaded; see `dictate models`)")
    bc.add_argument("--no-clips", action="store_true", help="skip the end-to-end check on recorded clips")

    args = p.parse_args(argv)
    handlers = {
        None: cmd_run,
        "run": cmd_run,
        "setup": cmd_setup,
        "devices": cmd_devices,
        "config": cmd_config,
        "lang": cmd_lang,
        "models": cmd_models,
        "transcribe": cmd_transcribe,
        "bench": cmd_bench,
    }
    handlers[args.cmd](args)


if __name__ == "__main__":
    main()
