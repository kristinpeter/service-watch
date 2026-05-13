"""Tests for config loading."""

from __future__ import annotations

from pathlib import Path

import pydantic
import pytest

from service_watch.config import load_config


def test_loads_minimal_valid_config(tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    p.write_text("services: []\n")
    cfg = load_config(p)
    assert cfg.check_interval_seconds == 60
    assert cfg.services == []


def test_loads_full_config(tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    p.write_text(
        """
check_interval_seconds: 30
repeat_interval_seconds: 1800
services:
  - name: api
    url: https://api.example.com/
    expect_text_contains: "ok"
    runbook_url: https://wiki.example.com/api
"""
    )
    cfg = load_config(p)
    assert cfg.check_interval_seconds == 30
    assert cfg.repeat_interval_seconds == 1800
    assert len(cfg.services) == 1
    assert cfg.services[0].name == "api"
    assert cfg.services[0].expect_text_contains == "ok"


def test_raises_value_error_on_non_dict_root(tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    p.write_text("- just\n- a\n- list\n")
    with pytest.raises(ValueError, match="must be a dictionary"):
        load_config(p)


def test_raises_validation_error_on_bad_url(tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    p.write_text("services:\n  - name: bad\n    url: not-a-url\n")
    with pytest.raises(pydantic.ValidationError):
        load_config(p)
