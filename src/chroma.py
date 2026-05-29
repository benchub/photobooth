"""Green-screen chroma key.

Two profiles share the same params (`ChromaKeyer`):

  key_preview()  fast path — downsample to 720p-ish, small kernels.
                 Used in the live preview loop at ~15-25 fps.

  key_final()    high-quality path — full-res, larger feather, optional
                 guided filter (opencv-contrib) for clean hair edges.
                 Used once per captured shot.

Both produce a uint8 BGR image. Background images are resized + center-
cropped to match the foreground frame's aspect.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np


@dataclass
class ChromaKeyer:
    hue_low: int = 35
    hue_high: int = 85
    sat_min: int = 60
    val_min: int = 40
    feather_px_preview: int = 5
    feather_px_final: int = 17
    spill_suppress: bool = True
    guided_filter: bool = True

    # Cache of the last fitted preview background. Backgrounds are often
    # full-res (e.g. 24MP), and `_fit_background` of one costs ~25ms — doing
    # that every preview frame is the difference between smooth and unusably
    # laggy. The fit result is identical frame-to-frame (same bg, same target
    # size), so we memoise it keyed by (bg identity, target size).
    _bg_cache_key: tuple[int, tuple[int, int], tuple[int, int]] | None = field(
        default=None, init=False, repr=False, compare=False,
    )
    _bg_cache_val: Any = field(default=None, init=False, repr=False, compare=False)

    def key_preview(self, frame_bgr: np.ndarray, bg_bgr: np.ndarray) -> np.ndarray:
        """Fast path for live preview. Frame can be any size; downsample if big."""
        h, w = frame_bgr.shape[:2]
        if h > 800:
            scale = 720 / h
            frame_bgr = cv2.resize(
                frame_bgr,
                (int(w * scale), 720),
                interpolation=cv2.INTER_AREA,
            )
        bg = self._fit_background_cached(bg_bgr, frame_bgr.shape[:2])
        mask = self._mask(frame_bgr, dilate=3)
        if self.feather_px_preview > 0:
            ksize = _odd(self.feather_px_preview * 2 + 1)
            mask = cv2.GaussianBlur(mask, (ksize, ksize), 0)
        fg = self._spill_suppressed(frame_bgr, mask) if self.spill_suppress else frame_bgr
        return _alpha_composite(fg, bg, mask)

    def key_final(self, frame_bgr: np.ndarray, bg_bgr: np.ndarray) -> np.ndarray:
        """High-quality path. Computes mask at half-res, upsamples, refines."""
        h, w = frame_bgr.shape[:2]
        bg = _fit_background(bg_bgr, (h, w))

        half = cv2.resize(frame_bgr, (w // 2, h // 2), interpolation=cv2.INTER_AREA)
        mask_half = self._mask(half, dilate=3)
        mask = cv2.resize(mask_half, (w, h), interpolation=cv2.INTER_LINEAR)

        if self.guided_filter and hasattr(cv2, "ximgproc"):
            try:
                mask = cv2.ximgproc.guidedFilter(
                    guide=frame_bgr, src=mask, radius=8, eps=1e-3,
                )
            except cv2.error:
                pass

        if self.feather_px_final > 0:
            ksize = _odd(self.feather_px_final * 2 + 1)
            mask = cv2.GaussianBlur(mask, (ksize, ksize), 0)

        fg = self._spill_suppressed(frame_bgr, mask) if self.spill_suppress else frame_bgr
        return _alpha_composite(fg, bg, mask)

    def _fit_background_cached(
        self, bg_bgr: np.ndarray, target_hw: tuple[int, int]
    ) -> np.ndarray:
        """`_fit_background` memoised across preview frames.

        Keyed by the bg array's identity + shape and the target size. The
        caller (PreviewWidget) holds the bg array alive for the whole preview
        session, so id() is stable and won't be recycled out from under us.
        """
        key = (id(bg_bgr), bg_bgr.shape[:2], target_hw)
        if key != self._bg_cache_key:
            self._bg_cache_val = _fit_background(bg_bgr, target_hw)
            self._bg_cache_key = key
        return self._bg_cache_val

    def _mask(self, frame_bgr: np.ndarray, dilate: int) -> np.ndarray:
        """Return a uint8 mask: 0=keep foreground, 255=replace with background."""
        hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
        green = cv2.inRange(
            hsv,
            np.array([self.hue_low, self.sat_min, self.val_min], dtype=np.uint8),
            np.array([self.hue_high, 255, 255], dtype=np.uint8),
        )
        if dilate > 0:
            k = cv2.getStructuringElement(cv2.MORPH_RECT, (dilate, dilate))
            green = cv2.morphologyEx(green, cv2.MORPH_CLOSE, k)
            green = cv2.morphologyEx(green, cv2.MORPH_OPEN, k)
        return green  # uint8 0/255

    def _spill_suppressed(self, frame_bgr: np.ndarray, mask: np.ndarray) -> np.ndarray:
        """Knock green tint out of foreground edges (where mask is partial)."""
        # Operate only where the mask says "mostly foreground" (mask < 240).
        fg_zone = mask < 240
        out = frame_bgr.copy()
        b = out[..., 0]
        g = out[..., 1]
        r = out[..., 2]
        max_br = np.maximum(b, r)
        new_g = np.minimum(g, max_br)
        g[fg_zone] = new_g[fg_zone]
        return out


def _fit_background(bg_bgr: np.ndarray, target_hw: tuple[int, int]) -> np.ndarray:
    """Resize + center-crop bg to match the foreground frame aspect."""
    th, tw = target_hw
    bh, bw = bg_bgr.shape[:2]
    if (bh, bw) == (th, tw):
        return bg_bgr
    target_aspect = tw / th
    bg_aspect = bw / bh
    if bg_aspect > target_aspect:
        # bg too wide — match heights, crop horizontally.
        new_h = th
        new_w = int(round(th * bg_aspect))
    else:
        new_w = tw
        new_h = int(round(tw / bg_aspect))
    resized = cv2.resize(bg_bgr, (new_w, new_h), interpolation=cv2.INTER_AREA)
    x = (new_w - tw) // 2
    y = (new_h - th) // 2
    return resized[y:y + th, x:x + tw]


def _alpha_composite(fg_bgr: np.ndarray, bg_bgr: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """mask=255 → bg, mask=0 → fg. Alpha blend in float32."""
    a = (mask.astype(np.float32) / 255.0)[..., None]
    out = fg_bgr.astype(np.float32) * (1.0 - a) + bg_bgr.astype(np.float32) * a
    return np.clip(out, 0, 255).astype(np.uint8)


def _odd(n: int) -> int:
    return n if n % 2 == 1 else n + 1
