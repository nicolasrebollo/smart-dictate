"""API keys for the online services: kept in the system keychain (Windows Credential Manager, macOS
Keychain), never in the settings file. Source checkouts can also use environment variables
(DICTATE_OPENAI_KEY, DICTATE_ANTHROPIC_KEY, DICTATE_GOOGLE_KEY, DICTATE_XAI_KEY)."""

from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

SERVICE = "Dictate"


def _env(provider: str) -> str:
    return os.environ.get(f"DICTATE_{provider.upper()}_KEY", "").strip()


def get_key(provider: str) -> str:
    if env := _env(provider):
        return env
    try:
        import keyring

        return keyring.get_password(SERVICE, provider) or ""
    except Exception:
        log.exception("couldn't read the %s key from the system keychain", provider)
        return ""


def set_key(provider: str, key: str) -> None:
    import keyring

    keyring.set_password(SERVICE, provider, key.strip())


def delete_key(provider: str) -> None:
    try:
        import keyring

        keyring.delete_password(SERVICE, provider)
    except Exception:
        log.debug("no %s key to delete", provider, exc_info=True)
