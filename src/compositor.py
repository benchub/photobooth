"""Full-res compositing + photo-strip assembly.

`composite()` — runs `ChromaKeyer.key_final()` on a captured JPEG against the
chosen background. Returns a PIL Image (RGB).

`make_strip()` — assembles 3 composites into a vertical photo-booth strip with
a header band ("{header_text} · YYYY-MM-DD") above. Output is digital-only
(~1200 px wide); not designed for print.
"""

from __future__ import annotations

import datetime as dt
import io
from pathlib import Path
from typing import Iterable

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .chroma import ChromaKeyer

STRIP_W = 1200
STRIP_MARGIN = 40
STRIP_GAP = 30
STRIP_HEADER_H = 200
PHOTO_ASPECT = 3 / 2

# Font candidates — first one that loads wins. Bundle one in assets/fonts/
# during M7; for now, fall back to system fonts.
FONT_CANDIDATES = [
    Path(__file__).resolve().parent.parent / "assets" / "fonts" / "BebasNeue-Regular.ttf",
    Path("/System/Library/Fonts/Supplemental/Impact.ttf"),
    Path("/System/Library/Fonts/Helvetica.ttc"),
    Path("/Library/Fonts/Arial Bold.ttf"),
]


def composite(
    capture_bgr: np.ndarray,
    background_path: Path,
    keyer: ChromaKeyer,
) -> Image.Image:
    """Key a single captured frame against a background. Returns RGB PIL Image."""
    bg = cv2.imread(str(background_path))
    if bg is None:
        raise FileNotFoundError(f"background not readable: {background_path}")
    out_bgr = keyer.key_final(capture_bgr, bg)
    rgb = cv2.cvtColor(out_bgr, cv2.COLOR_BGR2RGB)
    return Image.fromarray(rgb)


def make_strip(composites: Iterable[Image.Image], header_text: str) -> Image.Image:
    photos = list(composites)
    if not photos:
        raise ValueError("at least one composite required")

    photo_w = STRIP_W - 2 * STRIP_MARGIN
    photo_h = int(round(photo_w / PHOTO_ASPECT))
    total_h = (
        STRIP_MARGIN
        + STRIP_HEADER_H
        + len(photos) * (photo_h + STRIP_GAP)
        - STRIP_GAP
        + STRIP_MARGIN
    )

    strip = Image.new("RGB", (STRIP_W, total_h), (255, 255, 255))
    draw = ImageDraw.Draw(strip)

    # Header band — dark with bright text.
    header_box = (0, 0, STRIP_W, STRIP_HEADER_H)
    draw.rectangle(header_box, fill=(15, 15, 25))
    full_header = f"{header_text} · {dt.date.today().isoformat()}"
    _draw_header_text(draw, full_header)

    # Photos.
    y = STRIP_HEADER_H + STRIP_MARGIN
    for img in photos:
        scaled = _fit_photo(img, photo_w, photo_h)
        strip.paste(scaled, (STRIP_MARGIN, y))
        y += photo_h + STRIP_GAP

    return strip


def _draw_header_text(draw: ImageDraw.ImageDraw, text: str) -> None:
    font = _load_font(110)
    # Pillow 10.x: textbbox returns (l, t, r, b) of the rendered text.
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    x = (STRIP_W - tw) // 2 - bbox[0]
    y = (STRIP_HEADER_H - th) // 2 - bbox[1]
    draw.text((x, y), text, fill=(245, 245, 230), font=font)


def _load_font(size: int) -> ImageFont.ImageFont:
    for candidate in FONT_CANDIDATES:
        if candidate.exists():
            try:
                return ImageFont.truetype(str(candidate), size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def _fit_photo(img: Image.Image, w: int, h: int) -> Image.Image:
    """Resize + center-crop a photo to exactly (w, h)."""
    iw, ih = img.size
    target_aspect = w / h
    src_aspect = iw / ih
    if src_aspect > target_aspect:
        # source is wider — match height, crop width.
        new_h = h
        new_w = int(round(h * src_aspect))
    else:
        new_w = w
        new_h = int(round(w / src_aspect))
    resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    x = (new_w - w) // 2
    y = (new_h - h) // 2
    return resized.crop((x, y, x + w, y + h))


def jpeg_bytes_to_bgr(data: bytes) -> np.ndarray:
    """Decode JPEG bytes (from gphoto2) into a BGR numpy array."""
    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("could not decode JPEG bytes")
    return img


def save_jpeg(img: Image.Image, path: Path, quality: int = 92) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "JPEG", quality=quality)
    return path


__all__ = [
    "composite",
    "make_strip",
    "jpeg_bytes_to_bgr",
    "save_jpeg",
]
