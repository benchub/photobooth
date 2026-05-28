"""Full-res compositing + photo-strip assembly.

`composite()` — runs `ChromaKeyer.key_final()` on a captured JPEG against the
chosen background. Returns a PIL Image (RGB).

`make_strip()` — assembles 3 composites into a vertical photo-booth strip. The
title runs rotated down the left spine (so a long title gets the strip's full
height to breathe) and the date sits in a band across the top. Output is
digital-only (~1200 px wide); not designed for print.
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
STRIP_MARGIN = 40    # text padding inside the dark bands
STRIP_BORDER = 10    # white border around the photos
STRIP_GAP = 8        # white gap between photos
STRIP_DATE_H = 150   # top band, holds the date
STRIP_TITLE_W = 170  # left spine, holds the rotated title
PHOTO_ASPECT = 3 / 2

# Band colours.
BAND_FILL = (15, 15, 25)
BAND_TEXT = (245, 245, 230)

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

    content_left = STRIP_TITLE_W + STRIP_BORDER
    photo_w = STRIP_W - content_left - STRIP_BORDER
    photo_h = int(round(photo_w / PHOTO_ASPECT))
    total_h = (
        STRIP_DATE_H
        + STRIP_BORDER
        + len(photos) * (photo_h + STRIP_GAP)
        - STRIP_GAP
        + STRIP_BORDER
    )

    strip = Image.new("RGB", (STRIP_W, total_h), (255, 255, 255))
    draw = ImageDraw.Draw(strip)

    # Dark "L" frame: title spine down the left, date band across the top.
    draw.rectangle((0, 0, STRIP_TITLE_W, total_h), fill=BAND_FILL)
    draw.rectangle((0, 0, STRIP_W, STRIP_DATE_H), fill=BAND_FILL)

    _draw_date(draw, dt.date.today().isoformat())
    _draw_rotated_title(strip, header_text, (0, STRIP_DATE_H, STRIP_TITLE_W, total_h))

    # Photos.
    y = STRIP_DATE_H + STRIP_BORDER
    for img in photos:
        scaled = _fit_photo(img, photo_w, photo_h)
        strip.paste(scaled, (content_left, y))
        y += photo_h + STRIP_GAP

    return strip


def _draw_date(draw: ImageDraw.ImageDraw, text: str) -> None:
    """Centre the date horizontally in the top band."""
    font = _fit_font(text, STRIP_W - 2 * STRIP_MARGIN, start_size=110)
    # Pillow 10.x: textbbox returns (l, t, r, b) of the rendered text.
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    x = (STRIP_W - tw) // 2 - bbox[0]
    y = (STRIP_DATE_H - th) // 2 - bbox[1]
    draw.text((x, y), text, fill=BAND_TEXT, font=font)


def _draw_rotated_title(
    strip: Image.Image, text: str, region: tuple[int, int, int, int]
) -> None:
    """Render the title rotated 90° (reading bottom-to-top) down the left spine.

    The title's length runs along the strip's *height*, so even a long title has
    plenty of room; the font is shrunk only if it would overrun that length.
    """
    if not text.strip():
        return
    x0, y0, x1, y1 = region
    region_w, region_h = x1 - x0, y1 - y0
    avail_len = region_h - 2 * STRIP_MARGIN
    font = _fit_font(text, avail_len, start_size=130)

    # Draw horizontally onto a tight transparent layer, then rotate.
    bbox = font.getbbox(text)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    layer = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
    ImageDraw.Draw(layer).text((-bbox[0], -bbox[1]), text, fill=BAND_TEXT, font=font)
    rotated = layer.rotate(90, expand=True)

    rw, rh = rotated.size
    px = x0 + (region_w - rw) // 2
    py = y0 + (region_h - rh) // 2
    strip.paste(rotated, (px, py), rotated)


def _fit_font(text: str, max_w: int, start_size: int, min_size: int = 24) -> ImageFont.ImageFont:
    """Largest font (from start_size down) whose rendered text fits in max_w."""
    size = start_size
    while size > min_size:
        font = _load_font(size)
        bbox = font.getbbox(text)
        if bbox[2] - bbox[0] <= max_w:
            return font
        size -= 4
    return _load_font(min_size)


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
