"""runtime_overrides.yaml is loaded on top of config.yaml."""

from __future__ import annotations

from pathlib import Path

import pytest

from src import config as config_module
from src.config import (
    Config,
    clear_runtime_overrides,
    load_config,
    write_runtime_overrides,
)


@pytest.fixture
def patched_project_root(tmp_path: Path, monkeypatch):
    """Redirect PROJECT_ROOT so we don't touch the real project's config.yaml."""
    monkeypatch.setattr(config_module, "PROJECT_ROOT", tmp_path)
    # Minimal config.yaml the validator will accept.
    (tmp_path / "config.yaml").write_text(
        "immich:\n"
        "  base_url: 'http://x'\n"
        "  api_key: 'k'\n"
        "chroma:\n"
        "  hue_low: 35\n"
        "  hue_high: 85\n"
        "  sat_min: 60\n"
    )
    return tmp_path


def test_overrides_take_priority_over_config(patched_project_root):
    write_runtime_overrides({"chroma": {"hue_low": 50, "sat_min": 120}})
    cfg = load_config()
    assert cfg.chroma.hue_low == 50
    assert cfg.chroma.sat_min == 120
    # Unchanged keys still come from config.yaml.
    assert cfg.chroma.hue_high == 85


def test_clear_overrides_reverts_to_config(patched_project_root):
    write_runtime_overrides({"chroma": {"sat_min": 200}})
    cfg = load_config()
    assert cfg.chroma.sat_min == 200

    clear_runtime_overrides()
    cfg = load_config()
    assert cfg.chroma.sat_min == 60


def test_overrides_merge_with_existing(patched_project_root):
    write_runtime_overrides({"chroma": {"hue_low": 40}})
    write_runtime_overrides({"chroma": {"sat_min": 200}})
    cfg = load_config()
    assert cfg.chroma.hue_low == 40
    assert cfg.chroma.sat_min == 200
