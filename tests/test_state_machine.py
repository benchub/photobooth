"""Walk the BoothWindow state machine without a camera or display.

Run with: QT_QPA_PLATFORM=offscreen pytest tests/test_state_machine.py
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

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


def test_live_view_only_runs_in_preview_and_capture_states(app, cfg):
    """Battery saver: live view is resumed only for the pick/preview/countdown/
    capture states (pick pre-warms it) and paused for every idle state."""
    w = BoothWindow(cfg, enable_camera=False)
    w._camera_worker = MagicMock()

    live = {
        BoothState.PICK_BACKGROUND,
        BoothState.LIVE_PREVIEW,
        BoothState.COUNTDOWN,
        BoothState.CAPTURE,
    }
    for state in BoothState:
        w._camera_worker.reset_mock()
        w.transition_to(state)
        if state in live:
            w._camera_worker.resume_preview.assert_called()
            w._camera_worker.pause_preview.assert_not_called()
        else:
            w._camera_worker.pause_preview.assert_called()
            w._camera_worker.resume_preview.assert_not_called()


def test_space_advances_through_states(app, cfg):
    w = BoothWindow(cfg, enable_camera=False)
    assert w.state is BoothState.ATTRACT

    QTest.keyClick(w, Qt.Key.Key_Space)
    assert w.state is BoothState.PICK_BACKGROUND

    QTest.keyClick(w, Qt.Key.Key_Return)
    assert w.state is BoothState.LIVE_PREVIEW

    QTest.keyClick(w, Qt.Key.Key_Space)
    assert w.state is BoothState.COUNTDOWN


def test_startup_battery_alert_sent_once(app, cfg):
    w = BoothWindow(cfg, enable_camera=False)
    with patch("src.ui.booth_window.send_alert") as alert:
        w._on_battery(90, "90%")          # first reading -> one info text
        assert alert.call_count == 1
        assert "90%" in alert.call_args.args[1]
        assert w._battery_banner.isHidden()  # healthy: no banner
        w._on_battery(85, "85%")          # later readings: no repeat startup text
        assert alert.call_count == 1


def test_low_battery_shows_banner_and_texts_once(app, cfg):
    cfg.camera.battery_low_threshold_pct = 25
    w = BoothWindow(cfg, enable_camera=False)
    w._battery_startup_alert_sent = True  # isolate the low-battery dip behavior

    # isHidden() (not isVisible()) — the test window is never shown, so a
    # child's isVisible() is always False; isHidden() tracks the show/hide call.
    with patch("src.ui.booth_window.send_alert") as alert:
        w._on_battery(80, "80%")          # healthy: no banner, no text
        assert w._battery_banner.isHidden()
        alert.assert_not_called()

        w._on_battery(20, "20%")          # low: banner + one text
        assert not w._battery_banner.isHidden()
        assert "20%" in w._battery_banner.text()
        assert alert.call_count == 1

        w._on_battery(18, "18%")          # still low: no second text (latched)
        assert alert.call_count == 1

        w._on_battery(60, "60%")          # recovered (swap): banner clears, re-arm
        assert w._battery_banner.isHidden()
        w._on_battery(15, "15%")          # low again -> texts again
        assert alert.call_count == 2


def test_unparseable_battery_does_not_alert(app, cfg):
    w = BoothWindow(cfg, enable_camera=False)
    w._battery_startup_alert_sent = True  # isolate from the startup info text
    with patch("src.ui.booth_window.send_alert") as alert:
        w._on_battery(-1, "???")
        assert w._battery_banner.isHidden()
        alert.assert_not_called()


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
