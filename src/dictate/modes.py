"""Where the speech recognition runs, as chosen when Dictate was set up ([client] mode):

  remote  a PC that shares Dictate with you: connect to its link (wss://...)
  online  an online AI service: the engine runs inside this app (speech detection, spoken commands,
          formatting) and sends each phrase to the service you chose, with your API key
  local   this computer: the app's own copy of the server runs in the background, on the NVIDIA graphics
          card or on the processor alone ([client] local_device, see local_gpu.py)

Whatever the mode, the hotkey client always talks to a Dictate server over a WebSocket, so the
streaming, cancelling and status handling are the same everywhere.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

LOCAL = ("127.0.0.1", "localhost", "::1", "")


@dataclass
class Session:
    server: str
    token: str
    allow_hosts: list[str]  # the only hosts this process may connect to
    label: str  # shown in the menu, e.g. "Online: OpenAI + Claude"
    _stop: list[Any] = field(default_factory=list)

    def close(self) -> None:
        for stop in self._stop:
            try:
                stop()
            except Exception:
                log.debug("stopping failed", exc_info=True)


def online_config(cfg: Any) -> Any:
    """A copy of `cfg` with the engine pointed at the online services."""
    import copy

    c = copy.deepcopy(cfg)
    c.asr.backend = "cloud"
    c.asr.keep_warm = False
    c.cleanup.enabled = c.cleanup.enabled and bool(c.online.cleanup)
    c.cleanup.backend = "cloud"
    c.cleanup.timeout_ms = c.online.timeout_ms
    c.cleanup.incremental = True  # clean each finished sentence while you're still talking
    return c


def describe(cfg: Any) -> str:
    from dictate.cloud.providers import PROVIDERS

    mode = cfg.client.mode
    if mode == "online":
        speech = PROVIDERS.get(cfg.online.speech)
        cleanup = PROVIDERS.get(cfg.online.cleanup)
        names = [p.name for p in (speech, cleanup) if p]
        return "Online: " + " + ".join(dict.fromkeys(names)) if names else "Online"
    if mode == "local":
        return "This computer's processor" if cfg.client.local_device == "cpu" else "This computer's graphics card"
    host = urlsplit(cfg.client.server).hostname or ""
    return f"PC: {host.split('.')[0]}" if host not in LOCAL else "This PC"


def start(cfg: Any) -> Session:
    mode = cfg.client.mode
    if mode == "online":
        return _start_online(cfg)
    if mode == "local":
        return _start_local(cfg)
    host = urlsplit(cfg.client.server).hostname or ""
    token = cfg.client.token or (cfg.server.token if host in LOCAL else "")
    return Session(cfg.client.server, token, [host], describe(cfg))


def _start_online(cfg: Any) -> Session:
    from dictate import cloud, models
    from dictate.engine import Engine
    from dictate.server import DictateServer, start_in_thread

    ocfg = online_config(cfg)
    if not models.silero_path(ocfg).is_file():  # source checkouts: the 2 MB speech detector, hash-checked
        models.download_silero(ocfg)
    engine = Engine(ocfg)
    token = secrets.token_urlsafe(18)
    port = start_in_thread(DictateServer(engine, token), "127.0.0.1", 0)
    log.info("online engine ready (%s) on port %d", describe(cfg), port)
    return Session(f"ws://127.0.0.1:{port}", token, cloud.hosts_for(cfg), describe(cfg), [engine.close])


def _start_local(cfg: Any) -> Session:
    from dictate import local_gpu
    from dictate.config import ensure_server_token

    token = ensure_server_token(cfg)  # written to the settings file, where the server reads it
    server = local_gpu.LocalServer(cfg.server.port, cfg.client.local_device)
    return Session(f"ws://127.0.0.1:{cfg.server.port}", token, [], describe(cfg), [server.stop])
