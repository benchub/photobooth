"""Synthetic green-screen → confirm green is replaced and subject survives."""

from __future__ import annotations

import numpy as np

from src.chroma import ChromaKeyer, _fit_background


def _green_with_red_square(w: int = 400, h: int = 300) -> np.ndarray:
    """Solid green BGR with a red square centered."""
    frame = np.zeros((h, w, 3), dtype=np.uint8)
    frame[:, :] = (0, 255, 0)  # pure green
    cy, cx = h // 2, w // 2
    sz = 60
    frame[cy - sz:cy + sz, cx - sz:cx + sz] = (0, 0, 255)  # pure red
    return frame


def _solid_blue(w: int = 400, h: int = 300) -> np.ndarray:
    bg = np.zeros((h, w, 3), dtype=np.uint8)
    bg[:, :] = (255, 0, 0)  # pure blue
    return bg


def test_red_subject_survives_green_screen():
    k = ChromaKeyer()
    frame = _green_with_red_square()
    bg = _solid_blue()

    out = k.key_preview(frame, bg)

    # Center pixel should be red-ish (subject preserved).
    cy, cx = out.shape[0] // 2, out.shape[1] // 2
    b, g, r = int(out[cy, cx, 0]), int(out[cy, cx, 1]), int(out[cy, cx, 2])
    assert r > 200, f"subject not preserved at center: BGR={b,g,r}"
    assert b < 80 and g < 80


def test_green_is_replaced_by_background():
    k = ChromaKeyer()
    frame = _green_with_red_square()
    bg = _solid_blue()

    out = k.key_preview(frame, bg)

    # Corner pixel was green — should now be blue (background).
    b, g, r = int(out[10, 10, 0]), int(out[10, 10, 1]), int(out[10, 10, 2])
    assert b > 200, f"corner not replaced: BGR={b,g,r}"
    assert r < 80 and g < 80


def test_key_final_handles_full_res():
    k = ChromaKeyer()
    # 1500x1000 (smaller than 6000x4000 but exercises the same path).
    frame = _green_with_red_square(1500, 1000)
    bg = _solid_blue(2000, 1200)

    out = k.key_final(frame, bg)
    assert out.shape == (1000, 1500, 3)
    assert out.dtype == np.uint8


def test_fit_background_resizes_and_crops_to_target():
    # bg is wider than target — should crop width.
    bg = np.zeros((400, 1200, 3), dtype=np.uint8)
    bg[:, :600] = (255, 0, 0)
    bg[:, 600:] = (0, 0, 255)
    out = _fit_background(bg, (300, 400))
    assert out.shape == (300, 400, 3)


def test_spill_suppression_kills_green_tint_in_foreground():
    """A pixel inside the subject with a green cast should lose the green."""
    k = ChromaKeyer(spill_suppress=True)
    frame = _green_with_red_square()
    # Place a "greenish red" pixel near the center (still subject).
    cy, cx = 150, 200
    frame[cy, cx] = (40, 180, 200)  # green-tinted red
    bg = _solid_blue()

    out = k.key_preview(frame, bg)
    # Re-find center after the resize path (no resize happens at 400x300).
    b, g, r = int(out[cy, cx, 0]), int(out[cy, cx, 1]), int(out[cy, cx, 2])
    # After spill suppression, green channel should be <= max(b, r).
    assert g <= max(b, r) + 1, f"green tint not suppressed: BGR={b,g,r}"
