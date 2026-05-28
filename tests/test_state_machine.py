"""Walk the BoothWindow state machine without a camera or display.

Run with: QT_QPA_PLATFORM=offscreen pytest tests/test_state_machine.py
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from src.config import Config, ImmichConfig
from src.ui.booth_window import BoothState, BoothWindow


@pytest.fixture
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


@pytest.fixture
def cfg():
    c = Config()
    c.immich = ImmichConfig(base_url="http://x", api_key="x")
    # Keep tests fast.
    c.ui.countdown_seconds = 0
    c.ui.inactivity_timeout_s = 999
    c.ui.capture_count = 3
    # opencv-contrib's guidedFilter is not always thread-safe; disable for tests.
    c.chroma.guided_filter = False
    return c


def test_starts_in_attract(app, cfg):
    w = BoothWindow(cfg, enable_camera=False)
    assert w.state is BoothState.ATTRACT


def test_space_advances_through_states(app, cfg):
    w = BoothWindow(cfg, enable_camera=False)
    assert w.state is BoothState.ATTRACT

    QTest.keyClick(w, Qt.Key.Key_Space)
    assert w.state is BoothState.PICK_BACKGROUND

    QTest.keyClick(w, Qt.Key.Key_Return)
    assert w.state is BoothState.LIVE_PREVIEW

    QTest.keyClick(w, Qt.Key.Key_Space)
    assert w.state is BoothState.COUNTDOWN


def test_single_esc_does_not_quit(app, cfg):
    w = BoothWindow(cfg, enable_camera=False)
    QTest.keyClick(w, Qt.Key.Key_Escape)
    assert w.state is BoothState.ATTRACT


def test_triple_esc_quits(app, cfg, qtbot):
    w = BoothWindow(cfg, enable_camera=False)
    qtbot.addWidget(w)
    w.show()
    QTest.keyClick(w, Qt.Key.Key_Escape)
    QTest.keyClick(w, Qt.Key.Key_Escape)
    QTest.keyClick(w, Qt.Key.Key_Escape)
    qtbot.waitUntil(lambda: not w.isVisible(), timeout=1000)


def test_slow_esc_does_not_quit(app, cfg, qtbot):
    """Three Escs spread out over more than 1.5s shouldn't quit."""
    w = BoothWindow(cfg, enable_camera=False)
    qtbot.addWidget(w)
    w.show()
    QTest.keyClick(w, Qt.Key.Key_Escape)
    qtbot.wait(1600)
    QTest.keyClick(w, Qt.Key.Key_Escape)
    qtbot.wait(50)
    QTest.keyClick(w, Qt.Key.Key_Escape)
    qtbot.wait(100)
    # Should not have triggered close (only 2 within window).
    assert w.isVisible()


def test_inactivity_returns_to_attract(app, cfg, qtbot):
    cfg.ui.inactivity_timeout_s = 1
    w = BoothWindow(cfg, enable_camera=False)
    qtbot.addWidget(w)
    QTest.keyClick(w, Qt.Key.Key_Space)
    assert w.state is BoothState.PICK_BACKGROUND

    # Wait > timeout (1s + buffer).
    qtbot.wait(1300)
    assert w.state is BoothState.ATTRACT


def test_capture_cycle_ends_at_review(app, cfg, qtbot, tmp_path):
    # Provide a background so the synthesize-capture path can run.
    from PIL import Image
    bg = tmp_path / "bg.jpg"
    Image.new("RGB", (400, 300), "blue").save(bg)

    cfg.ui.countdown_seconds = 0
    w = BoothWindow(cfg, enable_camera=False, synthesize_when_no_camera=True)
    qtbot.addWidget(w)
    w.current_background = bg

    # Walk to LIVE_PREVIEW.
    QTest.keyClick(w, Qt.Key.Key_Space)
    QTest.keyClick(w, Qt.Key.Key_Return)
    assert w.state is BoothState.LIVE_PREVIEW

    # Space starts the capture cycle.
    QTest.keyClick(w, Qt.Key.Key_Space)

    # Wait for the cycle to finish into REVIEW.
    qtbot.waitUntil(lambda: w.state in (BoothState.REVIEW, BoothState.UPLOADING),
                    timeout=10000)


def test_capture_cycle_aborts_when_no_camera(app, cfg, qtbot):
    """Without a camera AND without dev synth, capture should abort cleanly."""
    cfg.ui.countdown_seconds = 0
    w = BoothWindow(cfg, enable_camera=False, synthesize_when_no_camera=False)
    qtbot.addWidget(w)

    QTest.keyClick(w, Qt.Key.Key_Space)        # → PICK
    QTest.keyClick(w, Qt.Key.Key_Return)       # → LIVE
    QTest.keyClick(w, Qt.Key.Key_Space)        # → COUNTDOWN
    qtbot.waitUntil(lambda: w.state is BoothState.DONE, timeout=5000)
