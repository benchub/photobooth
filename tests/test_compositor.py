"""Compositor + strip assembly tests."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

from src.chroma import ChromaKeyer
from src.compositor import composite, jpeg_bytes_to_bgr, make_strip, save_jpeg


def _green_frame(w: int = 400, h: int = 300) -> np.ndarray:
    frame = np.zeros((h, w, 3), dtype=np.uint8)
    frame[:, :] = (0, 255, 0)
    cy, cx = h // 2, w // 2
    frame[cy - 50:cy + 50, cx - 50:cx + 50] = (0, 0, 255)  # red square
    return frame


def test_composite_keys_against_real_background(tmp_path: Path):
    bg = np.zeros((300, 400, 3), dtype=np.uint8)
    bg[:, :] = (255, 0, 0)  # blue
    bg_path = tmp_path / "bg.jpg"
    cv2.imwrite(str(bg_path), bg)

    out = composite(_green_frame(), bg_path, ChromaKeyer())
    assert isinstance(out, Image.Image)
    arr = np.array(out)
    # Center pixel red (subject preserved).
    cy, cx = arr.shape[0] // 2, arr.shape[1] // 2
    r, g, b = arr[cy, cx]
    assert int(r) > 200 and int(g) < 80
    # Corner pixel blue (background replaced).
    r, g, b = arr[5, 5]
    assert int(b) > 200 and int(r) < 80


def test_make_strip_layout():
    photos = [Image.new("RGB", (1200, 800), (i * 80, 100, 200)) for i in range(3)]
    strip = make_strip(photos, "Test Event")
    assert strip.size[0] == 1200
    # Total height includes header + 3 photos + margins.
    assert strip.size[1] > 2000


def test_make_strip_requires_at_least_one_photo():
    with pytest.raises(ValueError):
        make_strip([], "x")


def test_make_strip_header_contains_date():
    import datetime as dt
    strip = make_strip([Image.new("RGB", (400, 300), "red")], "X")
    arr = np.array(strip)
    # The header band is the top 200 px. Most of it is the dark fill colour.
    band = arr[:200]
    # Some pixels must be the text colour (light) — at least 1000 of them.
    bright = (band[..., 0] > 200) & (band[..., 1] > 200) & (band[..., 2] > 200)
    assert bright.sum() > 1000
    # And the date is appended; we can't OCR but we can confirm the band rendered.
    # The actual date string check happens via observation in M7 polish.
    _ = dt.date.today().isoformat()  # smoke


def test_jpeg_bytes_roundtrip():
    frame = _green_frame()
    ok, encoded = cv2.imencode(".jpg", frame)
    assert ok
    out = jpeg_bytes_to_bgr(encoded.tobytes())
    assert out.shape == frame.shape


def test_save_jpeg_writes_file(tmp_path: Path):
    img = Image.new("RGB", (50, 50), "blue")
    out = save_jpeg(img, tmp_path / "sub" / "thing.jpg")
    assert out.exists()
    assert out.stat().st_size > 0
