"""CameraWorker — process check and import-fallback path.

Real hardware tests live in M3 verification (manual, with the R6 plugged in).
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from src.camera import CameraWorker, check_conflicting_processes, parse_battery_percent
from src.config import AlertsConfig, CameraConfig
from src.notify import send_sms_alert


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def test_check_conflicting_processes_returns_list():
    # The function must not raise, regardless of environment.
    result = check_conflicting_processes()
    assert isinstance(result, list)


def test_worker_constructs(app):
    w = CameraWorker(CameraConfig())
    assert w is not None


def test_worker_disconnects_when_gphoto_missing(app):
    """If python-gphoto2 fails to import, start() should emit disconnected."""
    w = CameraWorker(CameraConfig())

    received: list[str] = []
    w.disconnected.connect(lambda msg: received.append(msg))

    with patch("src.camera._GPHOTO_AVAILABLE", False):
        w.start()

    assert received
    assert "gphoto2" in received[0].lower()


def test_worker_blocks_on_conflicting_process(app):
    """If a conflicting process is detected, start emits disconnected."""
    w = CameraWorker(CameraConfig())

    received: list[str] = []
    w.disconnected.connect(lambda msg: received.append(msg))

    with patch("src.camera.check_conflicting_processes", return_value=["EOS Utility"]):
        w.start()

    assert received
    assert "EOS Utility" in received[0]


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("75%", 75),
        ("100%", 100),
        ("0%", 0),
        ("5 %", 5),
        ("250%", 100),   # clamped
        ("Low", 15),
        ("Normal", 75),
        ("Empty", 0),
        ("Full", 100),
        ("unknown", None),
        ("", None),
    ],
)
def test_parse_battery_percent(raw, expected):
    assert parse_battery_percent(raw) == expected


def test_send_sms_alert_noop_when_unconfigured():
    # No recipient / SMTP host configured → returns False and never sends.
    assert send_sms_alert(AlertsConfig(), "hi") is False
    assert send_sms_alert(AlertsConfig(sms_to="x@tmomail.net"), "hi") is False


def test_send_sms_alert_dispatches_when_configured():
    """When configured, it hands off to a background thread (no real SMTP)."""
    cfg = AlertsConfig(sms_to="5551234567@tmomail.net", smtp_host="smtp.test")
    with patch("src.notify.threading.Thread") as thread:
        assert send_sms_alert(cfg, "battery low") is True
    thread.assert_called_once()
    thread.return_value.start.assert_called_once()
