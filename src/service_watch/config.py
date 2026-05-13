"""Load and validate the YAML config into an AppConfig."""

from __future__ import annotations

from pathlib import Path

import yaml

from .types import AppConfig

DEFAULT_CONFIG_PATH = Path("/etc/service-watch/config.yaml")


def load_config(path: Path) -> AppConfig:
    content = path.read_text(encoding="utf-8")
    data = yaml.safe_load(content)
    if not isinstance(data, dict):
        raise ValueError("Config file root must be a dictionary")
    return AppConfig(**data)
