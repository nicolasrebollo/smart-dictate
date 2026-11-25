"""Standard per-user locations (Windows: %APPDATA%\\dictate and %LOCALAPPDATA%\\dictate)."""

from __future__ import annotations

import os
from pathlib import Path

from platformdirs import user_config_dir, user_data_dir, user_log_dir

APP_NAME = "dictate"


def config_dir() -> Path:
    if override := os.environ.get("DICTATE_CONFIG_DIR"):
        return Path(override)
    return Path(user_config_dir(APP_NAME, appauthor=False, roaming=True))


def config_path() -> Path:
    return config_dir() / "config.toml"


def data_dir() -> Path:
    if override := os.environ.get("DICTATE_DATA_DIR"):
        return Path(override)
    return Path(user_data_dir(APP_NAME, appauthor=False))


def default_models_dir() -> Path:
    return data_dir() / "models"


def log_dir() -> Path:
    if override := os.environ.get("DICTATE_DATA_DIR"):
        return Path(override) / "logs"
    return Path(user_log_dir(APP_NAME, appauthor=False))


def repo_root() -> Path:
    """Project checkout root (where bench/ fixtures live) when running from source."""
    return Path(__file__).resolve().parents[2]
