"""LLM backends for the cleanup pass."""

from dictate.llm.base import Completion, LLMBackend, LLMError, LLMTimeout, Message

__all__ = ["Completion", "LLMBackend", "LLMError", "LLMTimeout", "Message", "create_llm"]


def create_llm(cfg) -> LLMBackend:
    """Start (or connect to) the configured cleanup-model backend."""
    if cfg.cleanup.backend == "cloud":
        from dictate.cloud import create_cleanup

        return create_cleanup(cfg)
    if cfg.cleanup.backend == "ollama":
        from dictate.llm.ollama import OllamaBackend

        return OllamaBackend(cfg.llm.ollama_url, cfg.llm.ollama_model)
    from dictate import models, paths
    from dictate.llm.llama_server import LlamaServer

    log_dir = paths.log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    return LlamaServer(
        models.llama_server_exe(cfg),
        models.llm_path(cfg),
        context=cfg.llm.context,
        gpu_layers=cfg.llm.gpu_layers,
        port=cfg.llm.port,
        log_path=log_dir / "llama-server.log",
        extra_args=speculative_args(cfg),
    )


def speculative_args(cfg) -> list[str]:
    """Prompt-lookup speculative decoding: the cleaned text mostly copies the transcript, so drafting
    tokens from n-grams already in the prompt lets the model verify several per step."""
    if cfg.llm.speculative != "ngram":
        return []
    return [
        "--spec-type",
        "ngram-simple",
        "--spec-ngram-simple-size-n",
        str(cfg.llm.spec_ngram_n),
        "--spec-ngram-simple-size-m",
        str(cfg.llm.spec_ngram_m),
    ]
