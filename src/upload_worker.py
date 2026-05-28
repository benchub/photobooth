"""Background upload job. Runs on a plain threading.Thread.

QThread + moveToThread proved fragile for our concurrent-thread use case
(workers were hanging or crashing silently). PyQt6 signals are safe to
emit from any thread; with a QueuedConnection the slot runs on the GUI
thread, which is all we need.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

from .immich import ImmichClient, _enqueue

LOG = logging.getLogger(__name__)


class UploadJob(QObject):
    finished = pyqtSignal(int, int)  # succeeded, queued
    progress = pyqtSignal(str)

    def __init__(
        self,
        client: ImmichClient,
        files: list[Path],
        file_created_at: datetime,
        pending_dir: Path,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.client = client
        self.files = files
        self.created_at = file_created_at
        self.pending_dir = pending_dir
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="upload-job",
        )
        self._thread.start()

    def _run(self) -> None:
        try:
            self._do_run()
        except Exception:
            LOG.exception("upload job blew up")
            self.finished.emit(0, len(self.files))

    def _do_run(self) -> None:
        n = len(self.files)
        LOG.info("upload start: %d file(s)", n)
        self.progress.emit("Checking server…")

        ok, msg = self.client.health_check()
        if not ok:
            LOG.warning("immich health check failed: %s — queuing all %d files", msg, n)
            self.progress.emit(f"Server not reachable ({msg})")
            for f in self.files:
                if f.exists():
                    _enqueue(f, self.created_at, self.pending_dir)
            self.finished.emit(0, n)
            return
        LOG.info("immich health check ok: %s", msg)

        succeeded: list[str] = []
        for i, f in enumerate(self.files, 1):
            self.progress.emit(f"Uploading {i} of {n}…")
            LOG.info("uploading %s (%d bytes)", f.name, f.stat().st_size)
            try:
                result = self.client.upload_asset(f, self.created_at, retries=2)
                LOG.info("uploaded %s → %s (%s)", f.name, result.asset_id, result.status)
                succeeded.append(result.asset_id)
            except Exception as e:
                LOG.warning("upload of %s failed: %s — queuing remainder", f.name, e)
                for rest in self.files[i - 1:]:
                    if rest.exists():
                        _enqueue(rest, self.created_at, self.pending_dir)
                break

        if succeeded:
            self.progress.emit("Adding to album…")
            LOG.info("adding %d asset(s) to album", len(succeeded))
            try:
                self.client.add_to_album(succeeded)
            except Exception as e:
                LOG.warning("add_to_album failed: %s", e)

        queued = n - len(succeeded)
        LOG.info("upload done: %d ok, %d queued", len(succeeded), queued)
        self.finished.emit(len(succeeded), queued)


# Backwards-compatible alias for any callers still importing the old name.
UploadWorker = UploadJob
