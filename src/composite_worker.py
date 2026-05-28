"""Background compositing job. One-shot per capture.

Runs on a plain `threading.Thread` rather than a QThread — concurrent
composite QThreads were dying silently inside OpenCV in production, with
no traceback reaching the log. A plain daemon thread + emitting a Qt
signal at the end is more robust (PyQt6 signals are safe to emit from
non-Qt threads; with a QueuedConnection the slot runs on the GUI thread).
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

from .chroma import ChromaKeyer
from .compositor import composite, jpeg_bytes_to_bgr, save_jpeg

LOG = logging.getLogger(__name__)


class CompositeJob(QObject):
    finished = pyqtSignal(object)  # emits Path on success, None on failure

    def __init__(
        self,
        jpeg_bytes: bytes,
        background_path: Path,
        out_path: Path,
        keyer: ChromaKeyer,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._jpeg = jpeg_bytes
        self._bg = background_path
        self._out = out_path
        self._keyer = keyer
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="composite-job",
        )
        self._thread.start()

    def _run(self) -> None:
        try:
            LOG.info("composite start: bytes=%d → %s", len(self._jpeg), self._out.name)
            bgr = jpeg_bytes_to_bgr(self._jpeg)
            LOG.info("  decoded: shape=%s", bgr.shape)
            img = composite(bgr, self._bg, self._keyer)
            LOG.info("  composited: size=%s", img.size)
            save_jpeg(img, self._out, quality=92)
            LOG.info("  saved: %s (%d bytes)", self._out, self._out.stat().st_size)
            self.finished.emit(self._out)
        except Exception:
            LOG.exception("composite job failed")
            self.finished.emit(None)
