"""Immich REST client and pending-upload queue.

Endpoints (Immich 1.110+):
  POST  /api/assets             multipart upload
  GET   /api/albums?shared=false
  POST  /api/albums             create album
  PUT   /api/albums/{id}/assets add assets to album

Persistent state under ~/.photobooth/:
  device_id     stable per-install UUID, used as Immich `deviceId`
  album_id      cached UUID of the configured album

Failed uploads are moved to `output/pending_upload/` along with a sidecar
.meta.json; `drain_pending()` is called on app start to retry them.
"""

from __future__ import annotations

import json
import logging
import shutil
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import requests

LOG = logging.getLogger(__name__)


@dataclass
class UploadResult:
    asset_id: str
    status: str  # "created" or "duplicate"


class ImmichError(RuntimeError):
    pass


class ImmichClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        album_name: str,
        state_dir: Path,
        connect_timeout: float = 15.0,
        read_timeout: float = 60.0,
        device_label: str = "photobooth",
    ) -> None:
        self.base_url = self._normalize_base_url(base_url)
        self.api_key = api_key
        self.album_name = album_name
        self.state_dir = state_dir
        self.timeout: tuple[float, float] = (connect_timeout, read_timeout)
        self.device_label = device_label
        self._album_id: str | None = None
        self._device_id: str | None = None
        # Single Session: reuses the TCP/TLS connection between requests so
        # we don't pay a fresh handshake (and timeout risk) per asset.
        self._session = requests.Session()
        self._session.headers.update({
            "x-api-key": api_key,
            "Accept": "application/json",
        })
        LOG.info("ImmichClient base_url=%s timeout=%s",
                 self.base_url, self.timeout)

    @staticmethod
    def _normalize_base_url(raw: str) -> str:
        s = raw.strip().rstrip("/")
        if not s:
            return s
        if not s.startswith(("http://", "https://")):
            # Default to https; most Immich deploys are TLS.
            s = "https://" + s
        # Strip any "/api" the user may have appended — our methods add it.
        if s.endswith("/api"):
            s = s[:-4]
        return s

    def health_check(self, timeout: float = 8.0) -> tuple[bool, str]:
        """Quick liveness probe. Returns (ok, message). Used to fail fast
        before staging a multi-file upload session."""
        url = f"{self.base_url}/api/server/version"
        LOG.info("health_check: GET %s", url)
        try:
            r = self._session.get(url, timeout=(timeout, timeout))
        except requests.ConnectionError as e:
            return False, f"could not connect: {e.__class__.__name__}"
        except requests.Timeout:
            return False, f"timed out after {timeout}s"
        except requests.RequestException as e:
            return False, f"request error: {e.__class__.__name__}"
        if r.status_code == 401:
            return False, "401 — API key rejected"
        if r.status_code == 404:
            return False, "404 — endpoint not found (Immich version mismatch?)"
        if r.status_code >= 400:
            return False, f"HTTP {r.status_code}"
        return True, f"OK ({r.text[:80]})"

    # ------------------------------------------------------------------ device id

    @property
    def device_id(self) -> str:
        if self._device_id:
            return self._device_id
        self.state_dir.mkdir(parents=True, exist_ok=True)
        path = self.state_dir / "device_id"
        if path.exists():
            self._device_id = path.read_text().strip()
        else:
            self._device_id = f"{self.device_label}-{uuid.uuid4()}"
            path.write_text(self._device_id)
        return self._device_id

    # ------------------------------------------------------------------ uploads

    def upload_asset(
        self,
        file_path: Path,
        file_created_at: datetime,
        retries: int = 3,
    ) -> UploadResult:
        last_err: Exception | None = None
        for attempt in range(retries):
            try:
                return self._upload_once(file_path, file_created_at)
            except (requests.RequestException, ImmichError) as e:
                last_err = e
                if attempt == retries - 1:
                    break
                sleep = 2 ** attempt  # 1, 2, 4s
                LOG.warning(
                    "upload of %s failed (attempt %d/%d): %s — retry in %ds",
                    file_path.name, attempt + 1, retries, e, sleep,
                )
                time.sleep(sleep)
        raise ImmichError(f"upload failed after {retries} attempts: {last_err}")

    def _upload_once(self, file_path: Path, created_at: datetime) -> UploadResult:
        iso = _iso8601_utc(created_at)
        device_asset_id = f"{file_path.name}-{uuid.uuid4()}"
        with file_path.open("rb") as f:
            files = {"assetData": (file_path.name, f, "application/octet-stream")}
            data = {
                "deviceAssetId": device_asset_id,
                "deviceId": self.device_id,
                "fileCreatedAt": iso,
                "fileModifiedAt": iso,
            }
            r = self._session.post(
                f"{self.base_url}/api/assets",
                data=data,
                files=files,
                timeout=self.timeout,
            )
        if r.status_code >= 400:
            raise ImmichError(f"upload HTTP {r.status_code}: {_truncate(r.text)}")
        payload = r.json()
        return UploadResult(
            asset_id=payload["id"],
            status=payload.get("status", "created"),
        )

    # ------------------------------------------------------------------ albums

    def ensure_album(self) -> str:
        if self._album_id:
            return self._album_id
        self.state_dir.mkdir(parents=True, exist_ok=True)
        cache = self.state_dir / "album_id"
        if cache.exists():
            self._album_id = cache.read_text().strip()
            return self._album_id

        # Search by name.
        LOG.info("ensure_album: searching for %r", self.album_name)
        r = self._session.get(
            f"{self.base_url}/api/albums",
            params={"shared": "false"},
            timeout=self.timeout,
        )
        if r.status_code >= 400:
            raise ImmichError(f"album list HTTP {r.status_code}: {_truncate(r.text)}")
        for album in r.json():
            if album.get("albumName") == self.album_name:
                self._album_id = album["id"]
                cache.write_text(self._album_id)
                LOG.info("ensure_album: found existing %s", self._album_id)
                return self._album_id

        # Create it.
        LOG.info("ensure_album: creating %r", self.album_name)
        r = self._session.post(
            f"{self.base_url}/api/albums",
            json={"albumName": self.album_name, "assetIds": []},
            timeout=self.timeout,
        )
        if r.status_code >= 400:
            raise ImmichError(f"album create HTTP {r.status_code}: {_truncate(r.text)}")
        self._album_id = r.json()["id"]
        cache.write_text(self._album_id)
        LOG.info("ensure_album: created %s", self._album_id)
        return self._album_id

    def add_to_album(self, asset_ids: Iterable[str]) -> None:
        ids = list(asset_ids)
        if not ids:
            return
        album_id = self.ensure_album()
        LOG.info("add_to_album: PUT %d asset(s) into %s", len(ids), album_id)
        r = self._session.put(
            f"{self.base_url}/api/albums/{album_id}/assets",
            json={"ids": ids},
            timeout=self.timeout,
        )
        if r.status_code >= 400:
            raise ImmichError(f"album add HTTP {r.status_code}: {_truncate(r.text)}")

    # ------------------------------------------------------------------ pending queue

    def upload_session(
        self,
        files: list[Path],
        file_created_at: datetime,
        pending_dir: Path,
    ) -> list[UploadResult]:
        """Upload N files + add to album. On any failure, surviving files
        are moved to `pending_dir`. Returns whatever succeeded.

        Caller should treat any partial result as "queued" (or, equivalently,
        retry the whole batch on next start by draining pending_dir).
        """
        results: list[UploadResult] = []
        try:
            for f in files:
                results.append(self.upload_asset(f, file_created_at))
            self.add_to_album(r.asset_id for r in results)
            return results
        except (ImmichError, requests.RequestException) as e:
            LOG.warning("session upload failed mid-way: %s — queuing remainder", e)
            uploaded_set = {f.name for f in files[:len(results)]}
            for f in files:
                if f.name not in uploaded_set and f.exists():
                    _enqueue(f, file_created_at, pending_dir)
            return results

    def drain_pending(self, pending_dir: Path) -> tuple[int, int]:
        """Attempt to upload everything queued in pending_dir.

        Returns (succeeded, remaining). Files that succeed are deleted along
        with their sidecar. Files that fail stay put for next time.
        """
        if not pending_dir.exists():
            return (0, 0)
        succeeded = 0
        remaining = 0
        files = sorted(p for p in pending_dir.iterdir() if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
        uploaded: list[str] = []
        for f in files:
            meta_path = f.with_suffix(f.suffix + ".meta.json")
            try:
                meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
            except (OSError, json.JSONDecodeError):
                meta = {}
            created_at = _parse_iso(meta.get("file_created_at"))
            try:
                result = self.upload_asset(f, created_at)
                uploaded.append(result.asset_id)
                f.unlink(missing_ok=True)
                meta_path.unlink(missing_ok=True)
                succeeded += 1
            except (ImmichError, requests.RequestException) as e:
                LOG.warning("drain: %s still failing: %s", f.name, e)
                remaining += 1

        if uploaded:
            try:
                self.add_to_album(uploaded)
            except (ImmichError, requests.RequestException) as e:
                LOG.warning("drain: album add failed (assets still uploaded): %s", e)

        return (succeeded, remaining)


def _iso8601_utc(d: datetime) -> str:
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _parse_iso(s: str | None) -> datetime:
    if not s:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)


def _enqueue(file_path: Path, file_created_at: datetime, pending_dir: Path) -> None:
    pending_dir.mkdir(parents=True, exist_ok=True)
    dst = pending_dir / file_path.name
    if dst.exists():
        dst = pending_dir / f"{uuid.uuid4().hex[:8]}-{file_path.name}"
    shutil.move(str(file_path), dst)
    meta = {"file_created_at": _iso8601_utc(file_created_at), "original_name": file_path.name}
    dst.with_suffix(dst.suffix + ".meta.json").write_text(json.dumps(meta))


def _truncate(s: str, n: int = 200) -> str:
    return s if len(s) <= n else s[:n] + "…"
