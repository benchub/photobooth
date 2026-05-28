"""Generate procedural sample backgrounds.

Run once: `python tools/make_samples.py`. Writes 6000x4000 JPEGs to
backgrounds/ named `sample_*.jpg`. Safe to delete and re-run.
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

OUT_W, OUT_H = 6000, 4000
OUT_DIR = Path(__file__).resolve().parent.parent / "backgrounds"


def _vgrad(top: tuple[int, int, int], bot: tuple[int, int, int]) -> Image.Image:
    img = Image.new("RGB", (1, OUT_H))
    px = img.load()
    for y in range(OUT_H):
        t = y / (OUT_H - 1)
        px[0, y] = (
            int(top[0] + (bot[0] - top[0]) * t),
            int(top[1] + (bot[1] - top[1]) * t),
            int(top[2] + (bot[2] - top[2]) * t),
        )
    return img.resize((OUT_W, OUT_H))


def sample_sky() -> Image.Image:
    img = _vgrad((255, 200, 120), (100, 60, 180))  # sunset orange → purple
    d = ImageDraw.Draw(img)
    # Sun
    cx, cy, r = OUT_W // 2, int(OUT_H * 0.55), 600
    for i in range(r, 0, -10):
        a = int(255 * (1 - i / r) ** 2)
        d.ellipse(
            [cx - i, cy - i, cx + i, cy + i],
            fill=(255, 230, 150),
        )
    return img.filter(ImageFilter.GaussianBlur(radius=8))


def sample_space() -> Image.Image:
    img = _vgrad((10, 5, 40), (40, 5, 80))
    d = ImageDraw.Draw(img)
    # Random-ish star field via deterministic pattern.
    import random
    rnd = random.Random(42)
    for _ in range(1200):
        x, y = rnd.randint(0, OUT_W - 1), rnd.randint(0, OUT_H - 1)
        sz = rnd.choice([1, 1, 1, 2, 2, 3, 5, 8])
        b = rnd.randint(180, 255)
        d.ellipse([x, y, x + sz, y + sz], fill=(b, b, b))
    # A faint nebula blob.
    blob = Image.new("RGB", (OUT_W, OUT_H), (0, 0, 0))
    bd = ImageDraw.Draw(blob)
    bd.ellipse(
        [OUT_W // 3, OUT_H // 4, OUT_W * 2 // 3, OUT_H * 3 // 4],
        fill=(180, 60, 200),
    )
    blob = blob.filter(ImageFilter.GaussianBlur(radius=200))
    return Image.blend(img, blob, 0.35)


def sample_beach() -> Image.Image:
    sky = _vgrad((180, 220, 255), (240, 230, 200))
    sand_start = int(OUT_H * 0.6)
    sea_start = int(OUT_H * 0.45)
    d = ImageDraw.Draw(sky)
    # Sea band
    for y in range(sea_start, sand_start):
        t = (y - sea_start) / (sand_start - sea_start)
        c = (
            int(60 + 80 * t),
            int(130 + 60 * t),
            int(180 + 20 * t),
        )
        d.line([(0, y), (OUT_W, y)], fill=c)
    # Sand
    for y in range(sand_start, OUT_H):
        t = (y - sand_start) / (OUT_H - sand_start)
        c = (
            int(230 - 30 * t),
            int(210 - 40 * t),
            int(160 - 50 * t),
        )
        d.line([(0, y), (OUT_W, y)], fill=c)
    return sky.filter(ImageFilter.GaussianBlur(radius=2))


def sample_jungle() -> Image.Image:
    img = _vgrad((40, 100, 50), (10, 40, 20))
    d = ImageDraw.Draw(img)
    # Big leafy splotches.
    import random
    rnd = random.Random(7)
    for _ in range(120):
        cx = rnd.randint(0, OUT_W)
        cy = rnd.randint(0, OUT_H)
        rx = rnd.randint(200, 700)
        ry = rnd.randint(80, 300)
        g = rnd.randint(40, 130)
        d.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=(20, g, 30))
    return img.filter(ImageFilter.GaussianBlur(radius=12))


def sample_rainbow() -> Image.Image:
    img = Image.new("RGB", (OUT_W, OUT_H), (255, 255, 255))
    d = ImageDraw.Draw(img)
    colors = [
        (255, 80, 80),
        (255, 160, 60),
        (255, 230, 60),
        (80, 220, 100),
        (80, 160, 255),
        (160, 80, 230),
    ]
    band = OUT_H // len(colors)
    for i, c in enumerate(colors):
        d.rectangle([0, i * band, OUT_W, (i + 1) * band], fill=c)
    return img.filter(ImageFilter.GaussianBlur(radius=40))


def sample_party() -> Image.Image:
    img = _vgrad((20, 20, 40), (80, 30, 100))
    d = ImageDraw.Draw(img)
    import random
    rnd = random.Random(99)
    for _ in range(400):
        x = rnd.randint(0, OUT_W)
        y = rnd.randint(0, OUT_H)
        r = rnd.randint(30, 120)
        color = (
            rnd.randint(150, 255),
            rnd.randint(80, 200),
            rnd.randint(150, 255),
        )
        d.ellipse([x - r, y - r, x + r, y + r], fill=color)
    return img.filter(ImageFilter.GaussianBlur(radius=20))


SAMPLES = {
    "sample_sunset.jpg": sample_sky,
    "sample_space.jpg": sample_space,
    "sample_beach.jpg": sample_beach,
    "sample_jungle.jpg": sample_jungle,
    "sample_rainbow.jpg": sample_rainbow,
    "sample_party.jpg": sample_party,
}


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, fn in SAMPLES.items():
        path = OUT_DIR / name
        if path.exists():
            print(f"skip {path.name} (exists)")
            continue
        print(f"generating {path.name}…")
        img = fn()
        img.save(path, quality=88)
    print("done.")


if __name__ == "__main__":
    main()
