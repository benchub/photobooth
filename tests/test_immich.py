"""ImmichClient — mock the HTTP surface and verify happy + failure paths."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
import requests
import responses

from src.immich import ImmichClient, ImmichError


BASE = "https://immich.test"


@pytest.fixture
def client(tmp_path: Path) -> ImmichClient:
    return ImmichClient(
        base_url=BASE,
        api_key="testkey",
        album_name="Photobooth",
        state_dir=tmp_path / "state",
    )


@pytest.fixture
def fake_jpeg(tmp_path: Path) -> Path:
    p = tmp_path / "shot.jpg"
    p.write_bytes(b"\xff\xd8\xff\xe0not-a-real-jpeg")
    return p


@responses.activate
def test_upload_asset_success(client, fake_jpeg):
    responses.add(
        responses.POST, f"{BASE}/api/assets",
        json={"id": "asset-uuid-1", "status": "created"},
        status=201,
    )
    result = client.upload_asset(fake_jpeg, datetime(2026, 5, 28, 12, 0, tzinfo=timezone.utc))
    assert result.asset_id == "asset-uuid-1"
    assert result.status == "created"

    call = responses.calls[0]
    assert call.request.headers["x-api-key"] == "testkey"
    # multipart bodies include the field names
    body = call.request.body
    if hasattr(body, "decode"):
        body_str = body.decode("latin1", errors="replace")
    else:
        body_str = str(body)
    assert "deviceAssetId" in body_str
    assert "deviceId" in body_str
    assert "fileCreatedAt" in body_str


@responses.activate
def test_upload_retries_on_failure(client, fake_jpeg):
    responses.add(responses.POST, f"{BASE}/api/assets", status=500)
    responses.add(responses.POST, f"{BASE}/api/assets", status=500)
    responses.add(
        responses.POST, f"{BASE}/api/assets",
        json={"id": "id3", "status": "created"}, status=201,
    )
    # Disable real sleeps for speed.
    import src.immich as immich
    immich.time.sleep = lambda _s: None

    result = client.upload_asset(fake_jpeg, datetime.now(timezone.utc), retries=3)
    assert result.asset_id == "id3"
    assert len(responses.calls) == 3


@responses.activate
def test_upload_gives_up_after_n_retries(client, fake_jpeg):
    responses.add(responses.POST, f"{BASE}/api/assets", status=500)
    responses.add(responses.POST, f"{BASE}/api/assets", status=500)
    responses.add(responses.POST, f"{BASE}/api/assets", status=500)
    import src.immich as immich
    immich.time.sleep = lambda _s: None

    with pytest.raises(ImmichError):
        client.upload_asset(fake_jpeg, datetime.now(timezone.utc), retries=3)


@responses.activate
def test_ensure_album_finds_existing(client):
    responses.add(
        responses.GET, f"{BASE}/api/albums",
        json=[
            {"id": "other-uuid", "albumName": "Family"},
            {"id": "photobooth-uuid", "albumName": "Photobooth"},
        ],
        status=200,
    )
    album_id = client.ensure_album()
    assert album_id == "photobooth-uuid"
    # Cache the next call.
    album_id2 = client.ensure_album()
    assert album_id2 == "photobooth-uuid"
    assert len(responses.calls) == 1


@responses.activate
def test_ensure_album_creates_when_missing(client):
    responses.add(responses.GET, f"{BASE}/api/albums", json=[], status=200)
    responses.add(
        responses.POST, f"{BASE}/api/albums",
        json={"id": "new-album", "albumName": "Photobooth"},
        status=201,
    )
    assert client.ensure_album() == "new-album"


@responses.activate
def test_add_to_album_skips_empty(client):
    client.add_to_album([])  # no HTTP calls
    assert len(responses.calls) == 0


@responses.activate
def test_add_to_album_puts_ids(client):
    responses.add(responses.GET, f"{BASE}/api/albums", json=[
        {"id": "album", "albumName": "Photobooth"},
    ], status=200)
    responses.add(responses.PUT, f"{BASE}/api/albums/album/assets", json={}, status=200)
    client.add_to_album(["a1", "a2"])
    last = responses.calls[-1].request
    assert b'"ids"' in last.body
    assert b"a1" in last.body and b"a2" in last.body


@responses.activate
def test_upload_session_queues_failures(client, tmp_path):
    pending = tmp_path / "pending"
    files = [tmp_path / f"shot-{i}.jpg" for i in range(3)]
    for f in files:
        f.write_bytes(b"x")

    # First upload succeeds, second fails (after all retries).
    responses.add(responses.POST, f"{BASE}/api/assets",
                  json={"id": "ok-1", "status": "created"}, status=201)
    responses.add(responses.POST, f"{BASE}/api/assets", status=500)
    responses.add(responses.POST, f"{BASE}/api/assets", status=500)
    responses.add(responses.POST, f"{BASE}/api/assets", status=500)

    import src.immich as immich
    immich.time.sleep = lambda _s: None

    results = client.upload_session(files, datetime.now(timezone.utc), pending)
    assert len(results) == 1
    # The two remaining files should be queued.
    queued = sorted(p.name for p in pending.iterdir() if p.suffix == ".jpg")
    assert len(queued) == 2


@responses.activate
def test_drain_pending_uploads_remaining(client, tmp_path):
    pending = tmp_path / "pending"
    pending.mkdir()
    for i, name in enumerate(["x.jpg", "y.jpg"]):
        p = pending / name
        p.write_bytes(b"x")
        meta = {"file_created_at": "2026-05-28T12:00:00.000Z"}
        p.with_suffix(p.suffix + ".meta.json").write_text(json.dumps(meta))

    responses.add(responses.POST, f"{BASE}/api/assets",
                  json={"id": "u1", "status": "created"}, status=201)
    responses.add(responses.POST, f"{BASE}/api/assets",
                  json={"id": "u2", "status": "created"}, status=201)
    responses.add(responses.GET, f"{BASE}/api/albums", json=[
        {"id": "album", "albumName": "Photobooth"},
    ], status=200)
    responses.add(responses.PUT, f"{BASE}/api/albums/album/assets", json={}, status=200)

    succeeded, remaining = client.drain_pending(pending)
    assert succeeded == 2
    assert remaining == 0
    assert not (pending / "x.jpg").exists()
    assert not (pending / "x.jpg.meta.json").exists()
