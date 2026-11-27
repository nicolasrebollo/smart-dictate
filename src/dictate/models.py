"""Model download (the ONLY code path that uses the network) and presence checks."""

from __future__ import annotations

import hashlib
import logging
import os
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from dictate.config import Config

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class HFModel:
    repo: str
    revision: str  # pinned commit for reproducibility
    folder: str
    base_files: tuple[str, ...]
    variants: dict[str, tuple[str, ...]]  # quantization ("" = full precision) -> files


PARAKEET_BASE = ("config.json", "vocab.txt", "nemo128.onnx")
PARAKEET_VARIANTS = {
    "": ("encoder-model.onnx", "encoder-model.onnx.data", "decoder_joint-model.onnx"),
    "int8": ("encoder-model.int8.onnx", "decoder_joint-model.int8.onnx"),
}

ASR_MODELS: dict[str, HFModel] = {
    "nemo-parakeet-tdt-0.6b-v3": HFModel(
        "istupakov/parakeet-tdt-0.6b-v3-onnx",
        "8f23f0c03c8761650bdb5b40aaf3e40d2c15f1ce",
        "parakeet-tdt-0.6b-v3",
        PARAKEET_BASE,
        PARAKEET_VARIANTS,
    ),
    "nemo-parakeet-tdt-0.6b-v2": HFModel(
        "istupakov/parakeet-tdt-0.6b-v2-onnx",
        "0bbb45a3365852604aef28b538a8f066f4ccaa85",
        "parakeet-tdt-0.6b-v2",
        PARAKEET_BASE,
        PARAKEET_VARIANTS,
    ),
}

# Official Silero VAD v6.2.1 (MIT), straight from the upstream repo, hash-pinned.
SILERO_URL = "https://github.com/snakers4/silero-vad/raw/v6.2.1/src/silero_vad/data/silero_vad.onnx"
SILERO_SHA256 = "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3"


@dataclass(frozen=True)
class LLMModel:
    repo: str
    revision: str
    file: str
    size_gb: float
    note: str


# Cleanup model candidates (4-bit GGUF). The default is picked from `dictate bench cleanup`.
LLM_MODELS: dict[str, LLMModel] = {
    "qwen3.5-0.8b": LLMModel(
        "unsloth/Qwen3.5-0.8B-GGUF",
        "6ab461498e2023f6e3c1baea90a8f0fe38ab64d0",
        "Qwen3.5-0.8B-Q4_K_M.gguf",
        0.5,
        "fastest, weakest",
    ),
    "qwen3.5-2b": LLMModel(
        "unsloth/Qwen3.5-2B-GGUF",
        "f6d5376be1edb4d416d56da11e5397a961aca8ae",
        "Qwen3.5-2B-Q4_K_M.gguf",
        1.3,
        "less accurate",
    ),
    "qwen3.5-4b": LLMModel(
        "unsloth/Qwen3.5-4B-GGUF",
        "e87f176479d0855a907a41277aca2f8ee7a09523",
        "Qwen3.5-4B-Q4_K_M.gguf",
        2.7,
        "slower; tends to over-edit",
    ),
    "gemma-4-e2b": LLMModel(
        "unsloth/gemma-4-E2B-it-qat-GGUF",
        "66a399f68ddd113b06dff02fca9523e55465d11d",
        "gemma-4-E2B-it-qat-UD-Q4_K_XL.gguf",
        2.6,
        "default: most accurate + fastest in bench",
    ),
}

# Official llama.cpp Windows CUDA 13 build (CUDA runtime DLLs come from the nvidia-* pip wheels).
LLAMA_CPP_BUILD = "b12670"
LLAMA_CPP_URL = (
    f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_CPP_BUILD}/"
    f"llama-{LLAMA_CPP_BUILD}-bin-win-cuda-13.5-x64.zip"
)
LLAMA_CPP_SHA256 = "089a40c166e08794127ed815a1500e17821cb665dd39a120d369e02675ac7b44"


def llama_server_exe(cfg: Config) -> Path:
    return cfg.models_dir / "llama.cpp" / f"{LLAMA_CPP_BUILD}-cuda13" / "llama-server.exe"


def llm_path(cfg: Config, name: str | None = None) -> Path:
    spec = LLM_MODELS.get(name or cfg.cleanup.model)
    if spec is None:
        raise ValueError(f"unknown cleanup model {name or cfg.cleanup.model!r}; choose from {', '.join(LLM_MODELS)}")
    return cfg.models_dir / "llm" / spec.file


def silero_path(cfg: Config) -> Path:
    path = cfg.models_dir / "silero-vad" / "silero_vad.onnx"
    bundled = Path(getattr(sys, "_MEIPASS", "")) / "silero" / "silero_vad.onnx"
    if not path.is_file() and getattr(sys, "frozen", False) and bundled.is_file():
        return bundled  # the desktop app carries its own copy (2 MB) for online mode
    return path


def asr_dir(cfg: Config) -> Path:
    return cfg.models_dir / ASR_MODELS[cfg.asr.model].folder


def missing_models(cfg: Config) -> list[str]:
    missing = []
    if not silero_path(cfg).is_file():
        missing.append("Silero VAD")
    if cfg.asr.backend == "cloud":
        return missing  # online mode: speech recognition and cleanup run on the online service
    spec = ASR_MODELS.get(cfg.asr.model)
    if spec is None:
        missing.append(f"unknown ASR model {cfg.asr.model}")
    else:
        files = spec.base_files + spec.variants[cfg.asr.quantization]
        if not all((asr_dir(cfg) / f).is_file() for f in files):
            missing.append(f"{cfg.asr.model} ({cfg.asr.quantization or 'full precision'})")
    if cfg.cleanup.enabled and cfg.cleanup.backend == "llama-server":
        if not llama_server_exe(cfg).is_file():
            missing.append("llama.cpp server")
        try:
            if not llm_path(cfg).is_file():
                missing.append(f"cleanup model {cfg.cleanup.model}")
        except ValueError as e:
            missing.append(str(e))
    return missing


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(url: str, dest: Path) -> None:
    """Download over HTTPS, checking the certificate against the computer's own trusted list (Python's own
    list is missing or incomplete in some installs, e.g. python.org's on macOS)."""
    import shutil

    from dictate.client import tls_context

    with urllib.request.urlopen(url, timeout=60, context=tls_context("wss://")) as r, dest.open("wb") as f:
        shutil.copyfileobj(r, f, 1 << 20)


def download_silero(cfg: Config) -> Path:
    dest = silero_path(cfg)
    if dest.is_file() and _sha256(dest) == SILERO_SHA256:
        print(f"  Silero VAD: already present ({dest})")
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    print(f"  Silero VAD: downloading {SILERO_URL}")
    _download(SILERO_URL, tmp)
    digest = _sha256(tmp)
    if digest != SILERO_SHA256:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"Silero VAD checksum mismatch ({digest}); refusing to install")
    tmp.replace(dest)
    return dest


def download_asr(cfg: Config, model: str, quantizations: set[str]) -> Path:
    from huggingface_hub import snapshot_download

    spec = ASR_MODELS[model]
    files = list(spec.base_files)
    for q in sorted(quantizations):
        files += spec.variants[q]
    dest = cfg.models_dir / spec.folder
    variants = ", ".join(q or "fp32" for q in sorted(quantizations))
    print(f"  {model}: {spec.repo}@{spec.revision[:8]} ({variants})")
    snapshot_download(spec.repo, revision=spec.revision, local_dir=dest, allow_patterns=files)
    return dest


def download_llama_cpp(cfg: Config) -> Path:
    import zipfile

    exe = llama_server_exe(cfg)
    if exe.is_file():
        print(f"  llama.cpp {LLAMA_CPP_BUILD}: already present")
        return exe
    exe.parent.mkdir(parents=True, exist_ok=True)
    tmp = exe.parent.with_suffix(".zip.part")
    print(f"  llama.cpp {LLAMA_CPP_BUILD}: downloading {LLAMA_CPP_URL}")
    _download(LLAMA_CPP_URL, tmp)
    digest = _sha256(tmp)
    if digest != LLAMA_CPP_SHA256:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"llama.cpp checksum mismatch ({digest}); refusing to install")
    with zipfile.ZipFile(tmp) as z:
        z.extractall(exe.parent)
    tmp.unlink(missing_ok=True)
    return exe


def download_llm(cfg: Config, name: str) -> Path:
    from huggingface_hub import hf_hub_download

    spec = LLM_MODELS[name]
    dest = cfg.models_dir / "llm"
    print(f"  {name}: {spec.repo}/{spec.file} (~{spec.size_gb} GB)")
    return Path(hf_hub_download(spec.repo, spec.file, revision=spec.revision, local_dir=dest))


# Hugging Face's downloader (hf_xet) fetches each file in many parallel pieces; by default it holds back
# how many, which can leave a fast connection at a few MB/s. Full speed, and no usage reports.
FAST_DOWNLOADS = {"HF_XET_HIGH_PERFORMANCE": "1", "HF_HUB_DISABLE_TELEMETRY": "1", "HF_XET_TELEMETRY_ENABLED": "0"}


def run_setup(cfg: Config, *, all_variants: bool = False, llms: list[str] | None = None) -> None:
    os.environ.update(FAST_DOWNLOADS)  # before huggingface_hub is imported (it reads them once)
    for var in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"):
        os.environ.pop(var, None)
    print(f"Downloading models into {cfg.models_dir}")
    download_silero(cfg)
    quants = {"", "int8"} if all_variants else {cfg.asr.quantization}
    download_asr(cfg, cfg.asr.model, quants)
    if cfg.cleanup.backend == "llama-server":
        download_llama_cpp(cfg)
        for name in dict.fromkeys([cfg.cleanup.model, *(llms or [])]):
            download_llm(cfg, name)
    missing = missing_models(cfg)
    if missing:
        raise RuntimeError(f"setup incomplete, still missing: {', '.join(missing)}")
    print("Done. dictate now runs fully offline.")
