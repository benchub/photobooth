"""Photobooth entry point.

Boots a fullscreen, frameless QMainWindow that owns the booth state machine.
Cmd+Shift+Q exits. Holds a `caffeinate` subprocess so the Mac doesn't sleep
during a session.
"""

from __future__ import annotations

import atexit
import logging
import os
import subprocess
import sys

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from .config import ConfigError, load_config
from .logging_setup import setup as setup_logging
from .retention import prune_directory
from .ui.booth_window import BoothWindow


def _start_caffeinate() -> subprocess.Popen[bytes] | None:
    try:
        proc = subprocess.Popen(
            ["caffeinate", "-d", "-i", "-s"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        atexit.register(proc.terminate)
        return proc
    except FileNotFoundError:
        return None


def main() -> int:
    log_path = setup_logging()
    print(f"photobooth log: {log_path}", file=sys.stderr)

    try:
        cfg = load_config()
    except ConfigError as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 2

    caffeinate = _start_caffeinate()

    # Trim output dirs so a long-running booth doesn't fill the disk.
    keep = cfg.output.retain_count
    for d in (cfg.raw_dir, cfg.composite_dir, cfg.strips_dir):
        prune_directory(d, keep)

    app = QApplication.instance() or QApplication(sys.argv)
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeMenuBar, True)

    window = BoothWindow(cfg)
    window.showFullScreen()

    rc = app.exec()

    # Hard-exit once the event loop returns. The camera runs in a QThread that
    # blocks in uninterruptible libgphoto2 calls (init/capture/get_config); if
    # the user quits while one is in flight, closeEvent can't join the thread,
    # and letting Python tear down a still-running QThread raises
    # "QThread: Destroyed while thread is still running" → SIGABRT (the crash
    # popup). closeEvent already made a best-effort graceful stop; here we just
    # flush, drop caffeinate, and exit without running destructors so a stuck
    # gphoto2 call can't crash us on the way out. The OS reaps the thread/USB.
    logging.shutdown()
    sys.stderr.flush()
    if caffeinate is not None:
        try:
            caffeinate.terminate()
        except Exception:
            pass
    os._exit(rc)


if __name__ == "__main__":
    sys.exit(main())
