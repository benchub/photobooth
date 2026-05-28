from __future__ import annotations

import time
from pathlib import Path

from src.retention import prune_directory


def test_prune_keeps_newest_n(tmp_path: Path):
    for i in range(5):
        p = tmp_path / f"shot-{i}.jpg"
        p.write_bytes(b"x")
        time.sleep(0.01)  # ensure distinct mtimes

    assert prune_directory(tmp_path, keep=3) == 2

    remaining = sorted(p.name for p in tmp_path.iterdir())
    # Should keep the 3 newest (shot-2..4).
    assert remaining == ["shot-2.jpg", "shot-3.jpg", "shot-4.jpg"]


def test_prune_below_threshold_is_noop(tmp_path: Path):
    for i in range(3):
        (tmp_path / f"f{i}.jpg").write_bytes(b"x")
    assert prune_directory(tmp_path, keep=10) == 0


def test_prune_removes_sidecars(tmp_path: Path):
    for i in range(4):
        p = tmp_path / f"f{i}.jpg"
        p.write_bytes(b"x")
        p.with_suffix(".jpg.meta.json").write_text("{}")
        time.sleep(0.01)

    prune_directory(tmp_path, keep=2)
    # The two pruned sidecars are gone.
    sidecars = [p for p in tmp_path.iterdir() if p.suffix == ".json"]
    assert len(sidecars) == 2


def test_prune_handles_missing_directory(tmp_path: Path):
    assert prune_directory(tmp_path / "nope", keep=5) == 0
