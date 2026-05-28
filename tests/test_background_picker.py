"""Background picker scans + selects + navigates."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from src.config import Config, ImmichConfig
from src.ui.background_picker import BackgroundPicker


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def cfg_with_backgrounds(tmp_path: Path):
    c = Config()
    c.immich = ImmichConfig(base_url="http://x", api_key="x")
    bg_dir = tmp_path / "backgrounds"
    bg_dir.mkdir()
    # Three tiny dummy JPEGs.
    from PIL import Image
    for name in ("a.jpg", "b.jpg", "c.png"):
        Image.new("RGB", (200, 100), (i := hash(name) & 0xFF, i, i)).save(bg_dir / name)
    # Monkey-patch the property to point at our tmp dir.
    type(c).backgrounds_dir = property(lambda self, _d=bg_dir: _d)
    return c


def test_scans_supported_files(app, cfg_with_backgrounds):
    p = BackgroundPicker(cfg_with_backgrounds)
    paths = [pth.name for pth in p._paths]
    assert sorted(paths) == ["a.jpg", "b.jpg", "c.png"]


def test_arrow_navigation_wraps(app, cfg_with_backgrounds):
    p = BackgroundPicker(cfg_with_backgrounds)
    p._selected_index = 0
    p.handle_arrow(Qt.Key.Key_Left)
    assert p._selected_index == 2  # wraps to last

    p.handle_arrow(Qt.Key.Key_Right)
    assert p._selected_index == 0


def test_selected_path_property(app, cfg_with_backgrounds):
    p = BackgroundPicker(cfg_with_backgrounds)
    p._selected_index = 1
    assert p.selected_path is not None
    assert p.selected_path.name in {"a.jpg", "b.jpg", "c.png"}


def test_empty_backgrounds_dir_is_safe(app, tmp_path: Path):
    c = Config()
    c.immich = ImmichConfig(base_url="http://x", api_key="x")
    empty = tmp_path / "empty"
    empty.mkdir()
    type(c).backgrounds_dir = property(lambda self, _d=empty: _d)
    p = BackgroundPicker(c)
    assert p._paths == []
    assert p.selected_path is None
    # Arrow keys with no items must not crash.
    p.handle_arrow(Qt.Key.Key_Right)
