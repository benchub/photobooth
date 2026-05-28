"""Persistent background upload daemon.

Watches `output/pending_upload/` for files dropped by the session. Uploads
each to Immich (one at a time), adds it to the configured album, then
deletes the file. On any failure: backs off and retries.

Sessions don't run their own uploads anymore — they just move files into
the pending directory + call `notify()`.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QObject, pyqtSignal

from .immich import ImmichClient, _parse_iso

LOG = logging.getLogger(__name__)

# Extensions to upload (everything else in the dir is ignored — including
# the sidecar .meta.json files).
UPLOAD_EXTS = {".jpg", ".jpeg", ".png"}

POLL_IDLE_SECONDS = 30          # how often to scan when queue empty
BACKOFF_SECONDS = 30            # wait after a failure
INITIAL_HEALTH_TIMEOUT_S = 8.0


class UploadDaemon(QObject):
    queue_changed = pyqtSignal(int)   # pending file count
    status_changed = pyqtSignal(str)  # human-readable status

    def __init__(
        self,
        client_factory: Callable[[], ImmichClient],
        pending_dir: Path,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._client_factory = client_factory
        self._pending_dir = pending_dir
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._client: ImmichClient | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._wake.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="upload-daemon",
        )
        self._thread.start()
        LOG.info("upload daemon started")

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def notify(self) -> None:
        """Wake the daemon — call this after moving new files into pending_dir."""
        self._wake.set()

    def current_queue_size(self) -> int:
        return len(self._list_pending())

    # ------------------------------------------------------------------ run loop

    def _emit_status(self, msg: str) -> None:
        if self._stop.is_set():
            return
        try:
            self.status_changed.emit(msg)
        except RuntimeError:
            pass  # parent QObject was already deleted

    def _emit_queue(self, n: int) -> None:
        if self._stop.is_set():
            return
        try:
            self.queue_changed.emit(n)
        except RuntimeError:
            pass

    def _run(self) -> None:
        self._initial_health_check()

        backoff = 0
        while not self._stop.is_set():
            files = self._list_pending()
            self._emit_queue(len(files))

            if not files:
                self._emit_status("Idle")
                if self._wake.wait(timeout=POLL_IDLE_SECONDS):
                    self._wake.clear()
                continue

            if backoff > 0:
                self._emit_status(f"Backing off ({backoff}s) — {len(files)} pending")
                if self._wake.wait(timeout=backoff):
                    self._wake.clear()
                backoff = 0
                if self._stop.is_set():
                    break
                continue

            if not self._ensure_client():
                backoff = BACKOFF_SECONDS
                continue

            f = files[0]
            self._emit_status(f"Uploading {f.name} ({len(files)} left)")
            ok = self._upload_one(f)
            if not ok:
                backoff = BACKOFF_SECONDS

        LOG.info("upload daemon exiting")

    def _initial_health_check(self) -> None:
        if not self._ensure_client():
            self._emit_status("Immich client unavailable")
            return
        try:
            ok, msg = self._client.health_check(timeout=INITIAL_HEALTH_TIMEOUT_S)
        except Exception as e:
            LOG.warning("initial health check raised: %s", e)
            self._emit_status(f"Immich check failed ({e.__class__.__name__})")
            return
        if ok:
            LOG.info("upload daemon: Immich %s", msg)
            self._emit_status("Connected to Immich")
        else:
            LOG.warning("upload daemon: %s", msg)
            self._emit_status(f"Immich: {msg}")

    def _ensure_client(self) -> bool:
        if self._client is not None:
            return True
        try:
            self._client = self._client_factory()
            return True
        except Exception as e:
            LOG.warning("could not construct ImmichClient: %s", e)
            self._client = None
            return False

    def _list_pending(self) -> list[Path]:
        if not self._pending_dir.exists():
            return []
        return sorted(
            p for p in self._pending_dir.iterdir()
            if p.is_file() and p.suffix.lower() in UPLOAD_EXTS
        )

    def _upload_one(self, f: Path) -> bool:
        meta_path = f.with_suffix(f.suffix + ".meta.json")
        try:
            meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
        except Exception:
            meta = {}
        created_at = _parse_iso(meta.get("file_created_at"))

        assert self._client is not None  # _ensure_client returned True
        try:
            LOG.info("daemon: uploading %s (%d bytes)", f.name, f.stat().st_size)
            result = self._client.upload_asset(f, created_at, retries=2)
            LOG.info("daemon: uploaded %s → %s (%s)", f.name, result.asset_id, result.status)
            try:
                self._client.add_to_album([result.asset_id])
            except Exception as e:
                LOG.warning("daemon: add_to_album failed for %s: %s", f.name, e)
                # Asset is uploaded; album-add can be retried by hand. Don't
                # re-queue the asset (would create a duplicate).
            f.unlink(missing_ok=True)
            meta_path.unlink(missing_ok=True)
            return True
        except Exception as e:
            LOG.warning("daemon: upload of %s failed: %s", f.name, e)
            self._emit_status(f"Upload failed: {e.__class__.__name__}")
            return False


def enqueue_session_files(
    files: list[Path],
    file_created_at,
    pending_dir: Path,
) -> list[Path]:
    """Move session output into the pending_upload directory with metadata.

    Returns the new paths in pending_dir. Files that are already in
    pending_dir are left as-is.
    """
    from datetime import timezone

    if file_created_at.tzinfo is None:
        file_created_at = file_created_at.replace(tzinfo=timezone.utc)
    iso = file_created_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    pending_dir.mkdir(parents=True, exist_ok=True)
    new_paths = []
    for src in files:
        if src.parent == pending_dir:
            new_paths.append(src)
            continue
        dst = pending_dir / src.name
        if dst.exists():
            # Avoid clobber: rename source.
            import uuid
            dst = pending_dir / f"{uuid.uuid4().hex[:8]}-{src.name}"
        # Use copy + unlink-original semantics: keep the original in
        # output/composite/ or output/strips/ so the user has a local copy too.
        import shutil
        shutil.copy2(src, dst)
        meta = {"file_created_at": iso, "original_name": src.name}
        dst.with_suffix(dst.suffix + ".meta.json").write_text(json.dumps(meta))
        new_paths.append(dst)
    return new_paths
