"""Offline enforcement.

The daemon never needs the network: models are fetched once by `dictate setup`.
At runtime we (1) set the offline/telemetry env vars every ML library honours and
(2) install a Python-level socket guard that refuses any non-loopback connection.
The guard covers everything that goes through Python's `socket` module (huggingface_hub,
urllib, httpx, ...). Native code that opens sockets itself would bypass it; none of our
runtime dependencies do, and the acceptance check (Wi-Fi off) covers the rest.
"""

from __future__ import annotations

import contextlib
import ipaddress
import os
import socket
import threading
from collections import Counter
from collections.abc import Iterable, Iterator
from typing import Any

OFFLINE_ENV = {
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "HF_DATASETS_OFFLINE": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1",
    "DO_NOT_TRACK": "1",
}

_LOOPBACK_NAMES = {"localhost", "localhost.localdomain", "ip6-localhost"}


class NetworkBlockedError(ConnectionRefusedError):
    """Raised when code tries to reach a non-loopback address while offline mode is on."""


def enforce_offline_env() -> None:
    os.environ.update(OFFLINE_ENV)


def _is_loopback_host(host: Any, extra_allowed: frozenset[str]) -> bool:
    if isinstance(host, bytes):
        host = host.decode(errors="ignore")
    if not isinstance(host, str):
        return False
    host = host.strip("[]").lower()
    if host in _LOOPBACK_NAMES or host in extra_allowed:
        return True
    try:
        return ipaddress.ip_address(host.split("%")[0]).is_loopback
    except ValueError:
        return False


_original_connect = socket.socket.connect
_original_connect_ex = socket.socket.connect_ex
_original_getaddrinfo = socket.getaddrinfo
_installed = False
# Hosts allowed only for a moment (Check for updates: GitHub), and the addresses they resolved to.
_temporary: Counter[str] = Counter()
_temporary_resolved: set[str] = set()
_temporary_lock = threading.Lock()


@contextlib.contextmanager
def allowing(*hosts: str) -> Iterator[None]:
    """Let these hosts through the guard (if it's on) until the block ends; then they're refused again."""
    names = [h.lower() for h in hosts]
    with _temporary_lock:
        _temporary.update(names)
    try:
        yield
    finally:
        with _temporary_lock:
            _temporary.subtract(names)
            for name in names:
                if _temporary[name] <= 0:
                    del _temporary[name]
            if not _temporary:
                _temporary_resolved.clear()


def _temporarily_allowed() -> frozenset[str]:
    with _temporary_lock:
        return frozenset(_temporary)


def install_network_guard(allow_hosts: Iterable[str] = ()) -> None:
    """Refuse every outbound connection that is not to loopback or to an explicitly allowed host
    (the client allows only your PC). Addresses that an allowed name resolves to are allowed too."""
    global _installed
    allowed = frozenset(h.lower() for h in allow_hosts)
    resolved: set[str] = set()

    def _check(sock: socket.socket, address: Any) -> None:
        if sock.family not in (socket.AF_INET, socket.AF_INET6):
            return  # AF_UNIX etc. are local by definition
        host = address[0] if isinstance(address, tuple) else address
        name = str(host).lower()
        if (
            not _is_loopback_host(host, allowed | _temporarily_allowed())
            and name not in resolved
            and name not in _temporary_resolved
        ):
            raise NetworkBlockedError(f"dictate: refused connection to {host!r} (not your server)")

    def guarded_connect(self: socket.socket, address: Any) -> None:
        _check(self, address)
        return _original_connect(self, address)

    def guarded_connect_ex(self: socket.socket, address: Any) -> int:
        _check(self, address)
        return _original_connect_ex(self, address)

    def guarded_getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        if host is not None and not _is_loopback_host(host, allowed | _temporarily_allowed()):
            raise NetworkBlockedError(f"dictate: refused DNS lookup of {host!r} (not your server)")
        result = _original_getaddrinfo(host, *args, **kwargs)
        if host is not None and not _is_loopback_host(host, frozenset()):
            addresses = {str(info[4][0]).lower() for info in result}
            if _is_loopback_host(host, allowed):
                resolved.update(addresses)
            else:  # allowed only for the moment: refused again once the moment is over
                with _temporary_lock:
                    _temporary_resolved.update(addresses)
        return result

    socket.socket.connect = guarded_connect  # type: ignore[method-assign]
    socket.socket.connect_ex = guarded_connect_ex  # type: ignore[method-assign]
    socket.getaddrinfo = guarded_getaddrinfo
    _installed = True


def uninstall_network_guard() -> None:
    global _installed
    socket.socket.connect = _original_connect  # type: ignore[method-assign]
    socket.socket.connect_ex = _original_connect_ex  # type: ignore[method-assign]
    socket.getaddrinfo = _original_getaddrinfo
    _installed = False


def guard_installed() -> bool:
    return _installed
