"""CameraWorker — process check and import-fallback path.

Real hardware tests live in M3 verification (manual, with the R6 plugged in).
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from src.camera import CameraWorker, check_conflicting_processes
from src.config import CameraConfig


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
