"""ASR backends. Pick one from config; heavy imports happen lazily."""

from __future__ import annotations

from dictate.asr.base import ASRBackend, Transcript
from dictate.config import Config


def create_asr(cfg: Config) -> ASRBackend:
    if cfg.asr.backend == "cloud":
        from dictate.cloud import create_speech

        return create_speech(cfg)
    if cfg.asr.backend == "parakeet":
        from dictate.asr.parakeet import ParakeetASR

        return ParakeetASR(
            cfg.models_dir,
            cfg.asr.model,
            device=cfg.asr.device,
            quantization=cfg.asr.quantization,
            warmup_max_s=cfg.asr.warmup_max_s,
        )
    raise ValueError(f"unknown ASR backend {cfg.asr.backend!r} (available: parakeet, cloud)")


__all__ = ["ASRBackend", "Transcript", "create_asr"]
