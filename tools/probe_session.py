"""End-to-end pipeline test using the real camera, headless.

Connects to the R6, takes 3 captures with the first sample background, runs
each through the chroma key, builds a strip. Saves all artifacts under
output/. Does NOT upload — that's tested separately.
"""

from __future__ import annotations

import datetime as dt
import logging
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np

try:
    import gphoto2 as gp
except ImportError:
    print("python-gphoto2 not installed", file=sys.stderr)
    sys.exit(2)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image as PILImage
from src.chroma import ChromaKeyer
from src.compositor import composite, jpeg_bytes_to_bgr, make_strip, save_jpeg

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
LOG = logging.getLogger("probe_session")

ROOT = Path(__file__).resolve().parent.parent
BG_DIR = ROOT / "backgrounds"
OUT = ROOT / "output"


def kill_ptpcamerad() -> None:
    subprocess.run(["killall", "-9", "ptpcamerad"], capture_output=True, check=False)


def autodetect_with_kill_loop(timeout_s: float = 6.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        kill_ptpcamerad()
        try:
            cams = list(gp.Camera.autodetect())
        except Exception:
            cams = []
        if cams:
            return cams
        time.sleep(0.1)
    return []


def main() -> int:
    LOG.info("autodetecting…")
    cams = autodetect_with_kill_loop(6.0)
    if not cams:
        LOG.error("no camera detected")
        return 1
    LOG.info("found %s on %s", cams[0][0], cams[0][1])

    cam = gp.Camera()
    for i in range(3):
        try:
            cam.init()
            break
        except Exception as e:
            LOG.warning("init attempt %d failed: %s", i + 1, e)
            kill_ptpcamerad()
            time.sleep(0.3)
    else:
        LOG.error("init failed after 3 attempts")
        return 1
    LOG.info("camera connected")

    # Apply settings one at a time (set_config bulk fails on R6).
    for k, v in [("imageformat", "L"), ("capturetarget", "Internal RAM"), ("autopoweroff", "0")]:
        try:
            config = cam.get_config()
            w = config.get_child_by_name(k)
            w.set_value(v)
            cam.set_single_config(k, w)
            LOG.info("set %s=%s", k, v)
        except Exception as e:
            LOG.warning("could not set %s=%s: %s", k, v, e)

    # Pick a background.
    bg_path = next(iter(sorted(BG_DIR.glob("sample_*.jpg"))), None)
    if bg_path is None:
        LOG.error("no sample background found; run tools/make_samples.py first")
        cam.exit()
        return 1
    LOG.info("background: %s", bg_path.name)

    keyer = ChromaKeyer()
    composites = []
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")

    for i in range(3):
        LOG.info("capture %d…", i + 1)
        t0 = time.monotonic()
        cam_path = cam.capture(gp.GP_CAPTURE_IMAGE)
        cf = cam.file_get(cam_path.folder, cam_path.name, gp.GP_FILE_TYPE_NORMAL)
        jpeg = bytes(memoryview(cf.get_data_and_size()))
        t1 = time.monotonic()
        LOG.info("  captured %d MB in %.2fs", len(jpeg) // 1_000_000, t1 - t0)

        raw_path = OUT / "raw" / f"{ts}_session_{i + 1}.jpg"
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_bytes(jpeg)

        bgr = jpeg_bytes_to_bgr(jpeg)
        t2 = time.monotonic()
        comp = composite(bgr, bg_path, keyer)
        t3 = time.monotonic()
        LOG.info("  composited in %.2fs (size %dx%d)", t3 - t2, *comp.size)

        comp_path = OUT / "composite" / f"{ts}_session_{i + 1}.jpg"
        save_jpeg(comp, comp_path, quality=92)
        composites.append(comp_path)

    LOG.info("assembling strip…")
    strip = make_strip([PILImage.open(p) for p in composites], "Photobooth Probe")
    strip_path = OUT / "strips" / f"{ts}_strip.jpg"
    save_jpeg(strip, strip_path, quality=92)
    LOG.info("strip saved: %s (%dx%d)", strip_path, *strip.size)

    LOG.info("exiting camera")
    cam.exit()

    print()
    print("== ARTIFACTS ==")
    for p in composites:
        print(f"  composite: {p.relative_to(ROOT)}")
    print(f"  strip:     {strip_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
