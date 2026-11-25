"""NVIDIA Parakeet TDT 0.6B v3 via onnx-asr (ONNX Runtime; CUDA or CPU). No PyTorch.

Measured on RTX 4060 (see README): fp32 CUDA ~27 ms for 3 s of audio, ~91 ms for 15 s.

Two CUDA details matter a lot:
* cuDNN conv algorithm search must be HEURISTIC (+ max workspace). DEFAULT falls back to a
  slow conv path (~12x slower); EXHAUSTIVE re-benchmarks every new input length.
* Each new input length still costs ~30 ms extra the first time, so audio is zero-padded
  up to a 0.5 s bucket and all buckets up to `warmup_max_s` are compiled at startup.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from pathlib import Path

import numpy as np

from dictate.asr.base import Transcript
from dictate.vad import SAMPLE_RATE, FloatArray

log = logging.getLogger(__name__)

BUCKET_S = 0.5

MODEL_DIRS = {
    "nemo-parakeet-tdt-0.6b-v3": "parakeet-tdt-0.6b-v3",
    "nemo-parakeet-tdt-0.6b-v2": "parakeet-tdt-0.6b-v2",
}


def pad_to_bucket(audio: FloatArray, bucket_s: float = BUCKET_S) -> FloatArray:
    bucket = int(bucket_s * SAMPLE_RATE)
    target = max(bucket, math.ceil(len(audio) / bucket) * bucket)
    if target == len(audio):
        return audio
    return np.pad(audio, (0, target - len(audio))).astype(np.float32, copy=False)


class ParakeetASR:
    def __init__(
        self,
        models_dir: Path,
        model: str = "nemo-parakeet-tdt-0.6b-v3",
        *,
        device: str = "cuda",
        quantization: str = "",
        warmup_max_s: int = 12,
    ) -> None:
        import onnx_asr
        import onnxruntime as ort

        ort.set_default_logger_severity(3)
        if model not in MODEL_DIRS:
            raise ValueError(f"unsupported Parakeet model {model!r}; choose one of {list(MODEL_DIRS)}")
        model_dir = models_dir / MODEL_DIRS[model]
        if not model_dir.is_dir():
            raise FileNotFoundError(f"model not found at {model_dir} - run `dictate setup` first")

        providers: list = ["CPUExecutionProvider"]
        if device == "cuda":
            if "CUDAExecutionProvider" not in ort.get_available_providers():
                log.warning("CUDA is not available to ONNX Runtime; falling back to CPU")
            else:
                ort.preload_dlls()  # CUDA/cuDNN DLLs from the nvidia-* pip wheels
                cuda_opts = {
                    "device_id": 0,
                    "cudnn_conv_algo_search": "HEURISTIC",
                    "cudnn_conv_use_max_workspace": "1",
                    "arena_extend_strategy": "kSameAsRequested",
                }
                providers = [("CUDAExecutionProvider", cuda_opts), "CPUExecutionProvider"]

        sess = ort.SessionOptions()
        sess.log_severity_level = 3
        self._model = onnx_asr.load_model(
            model, model_dir, quantization=quantization or None, providers=providers, sess_options=sess
        )
        self.name = f"parakeet:{model}:{device}{':' + quantization if quantization else ''}"
        self.device = "cuda" if len(providers) > 1 else "cpu"
        self._warmup_max_s = warmup_max_s

    def transcribe(self, audio: FloatArray, *, vocabulary: Sequence[str] = ()) -> Transcript:
        # Parakeet has no prompt/hotword biasing; vocabulary is applied by cleanup + replacements.
        if len(audio) < SAMPLE_RATE // 10:
            return Transcript(text="")
        text = self._model.recognize(pad_to_bucket(audio), sample_rate=SAMPLE_RATE)
        return Transcript(text=text.strip())

    def warmup(self) -> None:
        rng = np.random.default_rng(0)
        max_buckets = int(self._warmup_max_s / BUCKET_S) if self.device == "cuda" else 2
        for i in range(1, max_buckets + 1):
            n = int(i * BUCKET_S * SAMPLE_RATE)
            self._model.recognize((rng.standard_normal(n) * 0.01).astype(np.float32), sample_rate=SAMPLE_RATE)
