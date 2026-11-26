"""The runtime must never reach the network (only loopback is allowed)."""

import socket

import numpy as np
import pytest

from dictate import offline
from dictate.config import parse_config


@pytest.fixture
def guard():
    offline.install_network_guard()
    yield
    offline.uninstall_network_guard()


def test_blocks_public_ip(guard) -> None:
    s = socket.socket()
    try:
        with pytest.raises(offline.NetworkBlockedError):
            s.connect(("1.1.1.1", 443))
    finally:
        s.close()


def test_blocks_dns_lookup(guard) -> None:
    with pytest.raises(offline.NetworkBlockedError):
        socket.getaddrinfo("huggingface.co", 443)


def test_blocks_create_connection(guard) -> None:
    with pytest.raises(offline.NetworkBlockedError):
        socket.create_connection(("8.8.8.8", 53), timeout=1)


def test_allows_loopback(guard) -> None:
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(5)
    port = server.getsockname()[1]
    client = socket.socket()
    try:
        client.connect(("127.0.0.1", port))
        client.close()
        client = socket.create_connection(("localhost", port), timeout=1)
    finally:
        client.close()
        server.close()


def test_env_vars() -> None:
    offline.enforce_offline_env()
    import os

    assert os.environ["HF_HUB_OFFLINE"] == "1"
    assert os.environ["HF_HUB_DISABLE_TELEMETRY"] == "1"


@pytest.mark.slow
def test_full_pipeline_runs_with_network_blocked(guard) -> None:
    """Load the real models and transcribe with the guard on (skipped if models aren't installed)."""
    from dictate import models

    cfg = parse_config("")
    cfg.asr.device = "cpu"
    cfg.asr.quantization = "int8"
    cfg.asr.warmup_max_s = 1
    if models.missing_models(cfg):
        pytest.skip("models not installed (run `dictate setup --all`)")
    offline.enforce_offline_env()
    from dictate.asr import create_asr
    from dictate.pipeline import Pipeline, vad_probs_offline
    from dictate.vad import SAMPLE_RATE, SileroVAD

    vad = SileroVAD(models.silero_path(cfg))
    asr = create_asr(cfg)
    audio = (np.random.default_rng(0).standard_normal(SAMPLE_RATE * 2) * 0.001).astype(np.float32)
    res = Pipeline(cfg, asr).process(audio, vad_probs_offline(vad, audio))
    assert res.no_speech  # silence in, nothing out - and no network touched


def test_allowed_host_and_its_addresses_pass(monkeypatch) -> None:
    calls = []

    def fake_getaddrinfo(host, *a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("100.64.1.2", 443))]

    # pytest puts the real socket functions back afterwards, whatever uninstall restores
    monkeypatch.setattr(socket, "getaddrinfo", socket.getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", socket.socket.connect)
    monkeypatch.setattr(offline, "_original_getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(offline, "_original_connect", lambda self, addr: calls.append(addr))
    offline.install_network_guard(allow_hosts=["my-pc.tail1234.ts.net"])
    try:
        assert socket.getaddrinfo("my-pc.tail1234.ts.net", 443)[0][4][0] == "100.64.1.2"
        s = socket.socket()
        s.connect(("100.64.1.2", 443))  # resolved from the allowed name: OK
        s.close()
        assert calls == [("100.64.1.2", 443)]
        with pytest.raises(offline.NetworkBlockedError):
            socket.getaddrinfo("example.com", 443)
    finally:
        offline.uninstall_network_guard()
