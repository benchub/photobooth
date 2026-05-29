#!/usr/bin/env python3
"""Upscale images ~4x with Real-ESRGAN, for making AI-art backgrounds.

Most AI image models top out around 1024-1536px on the long edge. This booth
wants 6000x4000 backgrounds (see tools/make_samples.py). This tool runs the
Real-ESRGAN x4plus model to 4x an image, then fits it to the target size.

This pulls in a heavy PyTorch dependency that the booth itself does NOT need,
so its requirements live in tools/requirements-upscale.txt. Install into any
Python env (a separate venv is fine):

    python -m venv .venv-upscale
    .venv-upscale/bin/pip install -r tools/requirements-upscale.txt

Usage:
    # one file -> backgrounds/ (fit to 6000x4000, center-cropped)
    python tools/upscale.py my_art.png

    # explicit output, no resize (raw 4x)
    python tools/upscale.py in.png -o out.png --size none

    # a whole folder
    python tools/upscale.py incoming/ -o backgrounds/

The model weights (~65MB) download once to tools/.cache/ on first run.
"""

from __future__ import annotations

import argparse
import sys
import urllib.request
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from spandrel import ImageModelDescriptor, ModelLoader

# Real-ESRGAN x4plus: the general-purpose 4x model, good for photos and art.
WEIGHTS_URL = (
    "https://github.com/xinntao/Real-ESRGAN/releases/download/"
    "v0.1.0/RealESRGAN_x4plus.pth"
)
CACHE_DIR = Path(__file__).resolve().parent / ".cache"
WEIGHTS_PATH = CACHE_DIR / "RealESRGAN_x4plus.pth"

# Default target matches make_samples.py / the booth's background size.
DEFAULT_W, DEFAULT_H = 6000, 4000
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def pick_device(requested: str) -> torch.device:
    if requested != "auto":
        return torch.device(requested)
    if torch.cuda.is_available():
        return torch.device("cuda")
    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def download_weights() -> Path:
    if WEIGHTS_PATH.exists():
        return WEIGHTS_PATH
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _log(f"Downloading Real-ESRGAN weights -> {WEIGHTS_PATH} …")
    tmp = WEIGHTS_PATH.with_suffix(".part")
    urllib.request.urlretrieve(WEIGHTS_URL, tmp)  # noqa: S310 (trusted GitHub release)
    tmp.replace(WEIGHTS_PATH)
    return WEIGHTS_PATH


def load_model(device: torch.device) -> ImageModelDescriptor:
    model = ModelLoader().load_from_file(str(download_weights()))
    if not isinstance(model, ImageModelDescriptor):
        raise SystemExit("Loaded weights are not a single-image upscaling model.")
    model.to(device).eval()
    return model


@torch.inference_mode()
def upscale_tiled(
    model: ImageModelDescriptor,
    img: Image.Image,
    device: torch.device,
    tile: int,
    pad: int,
) -> Image.Image:
    """4x an image in overlapping tiles so memory stays bounded.

    Tiling happens in input-pixel space; `pad` is the overlap (in input px)
    trimmed from each tile after upscaling to hide seams.
    """
    scale = model.scale
    arr = np.asarray(img.convert("RGB"), dtype=np.float32) / 255.0
    h, w, _ = arr.shape
    out = np.zeros((h * scale, w * scale, 3), dtype=np.float32)

    n_tiles = ((h + tile - 1) // tile) * ((w + tile - 1) // tile)
    done = 0
    for y in range(0, h, tile):
        for x in range(0, w, tile):
            # Padded read region, clamped to image bounds.
            x0, y0 = max(x - pad, 0), max(y - pad, 0)
            x1, y1 = min(x + tile + pad, w), min(y + tile + pad, h)
            patch = arr[y0:y1, x0:x1]
            t = torch.from_numpy(patch).permute(2, 0, 1).unsqueeze(0).to(device)
            up = model(t).clamp(0, 1).squeeze(0).permute(1, 2, 0)
            up_np = up.float().cpu().numpy()

            # Map the (unpadded) tile interior back into the output canvas.
            ix0, iy0 = x, y
            ix1, iy1 = min(x + tile, w), min(y + tile, h)
            # Offset of the interior within the padded patch, all *scale.
            off_x, off_y = (ix0 - x0) * scale, (iy0 - y0) * scale
            sw, sh = (ix1 - ix0) * scale, (iy1 - iy0) * scale
            out[iy0 * scale : iy0 * scale + sh, ix0 * scale : ix0 * scale + sw] = (
                up_np[off_y : off_y + sh, off_x : off_x + sw]
            )

            done += 1
            _log(f"  tile {done}/{n_tiles}")

    return Image.fromarray(np.round(out * 255).astype(np.uint8), "RGB")


def fit_to(img: Image.Image, target: tuple[int, int], mode: str) -> Image.Image:
    """Resize `img` to `target` (w, h). cover = fill + center-crop."""
    tw, th = target
    if mode == "exact":
        return img.resize(target, Image.Resampling.LANCZOS)
    sw, sh = img.size
    if mode == "cover":
        f = max(tw / sw, th / sh)
    elif mode == "contain":
        f = min(tw / sw, th / sh)
    else:
        raise ValueError(mode)
    resized = img.resize((round(sw * f), round(sh * f)), Image.Resampling.LANCZOS)
    if mode == "contain":
        return resized
    # cover: center-crop to target
    rw, rh = resized.size
    left, top = (rw - tw) // 2, (rh - th) // 2
    return resized.crop((left, top, left + tw, top + th))


def process_one(
    model: ImageModelDescriptor,
    device: torch.device,
    src: Path,
    dst: Path,
    args: argparse.Namespace,
) -> None:
    _log(f"{src.name}: loading…")
    img = Image.open(src)
    _log(f"{src.name}: {img.size[0]}x{img.size[1]} -> upscaling {model.scale}x")
    up = upscale_tiled(model, img, device, args.tile, args.pad)

    if args.size != "none":
        up = fit_to(up, args.target, args.fit)

    dst.parent.mkdir(parents=True, exist_ok=True)
    save_kwargs = {"quality": 95} if dst.suffix.lower() in {".jpg", ".jpeg"} else {}
    up.save(dst, **save_kwargs)
    _log(f"{src.name}: wrote {dst} ({up.size[0]}x{up.size[1]})")


def parse_size(s: str) -> tuple[int, int]:
    if s == "none":
        return (0, 0)
    try:
        w, h = s.lower().split("x")
        return (int(w), int(h))
    except ValueError:
        raise argparse.ArgumentTypeError(f"--size must be WxH or 'none', got {s!r}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("input", type=Path, help="Image file or a directory of images.")
    p.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output file (single input) or directory. "
        "Defaults to backgrounds/<name>.jpg.",
    )
    p.add_argument(
        "--size",
        default=f"{DEFAULT_W}x{DEFAULT_H}",
        help="Final WxH after upscaling, or 'none' to keep raw 4x output. "
        f"Default {DEFAULT_W}x{DEFAULT_H}.",
    )
    p.add_argument(
        "--fit",
        choices=("cover", "contain", "exact"),
        default="cover",
        help="How to reach --size. cover (default) fills then center-crops.",
    )
    p.add_argument("--tile", type=int, default=512, help="Tile size in input px (default 512).")
    p.add_argument("--pad", type=int, default=32, help="Tile overlap in input px (default 32).")
    p.add_argument("--device", default="auto", help="auto|cpu|mps|cuda (default auto).")
    args = p.parse_args(argv)

    args.target = parse_size(args.size)

    inp: Path = args.input
    if not inp.exists():
        _log(f"error: {inp} not found")
        return 1

    # Resolve the source -> destination file list.
    bg_dir = Path(__file__).resolve().parent.parent / "backgrounds"
    jobs: list[tuple[Path, Path]] = []
    if inp.is_dir():
        srcs = sorted(f for f in inp.iterdir() if f.suffix.lower() in IMAGE_EXTS)
        if not srcs:
            _log(f"error: no images found in {inp}")
            return 1
        out_dir = args.output or bg_dir
        if out_dir.suffix:
            _log("error: output must be a directory when input is a directory")
            return 1
        jobs = [(s, out_dir / f"{s.stem}.jpg") for s in srcs]
    else:
        if args.output is None:
            dst = bg_dir / f"{inp.stem}.jpg"
        elif args.output.suffix:
            dst = args.output
        else:
            dst = args.output / f"{inp.stem}.jpg"
        jobs = [(inp, dst)]

    device = pick_device(args.device)
    _log(f"device: {device}")
    model = load_model(device)

    for src, dst in jobs:
        process_one(model, device, src, dst, args)

    _log(f"done: {len(jobs)} image(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
