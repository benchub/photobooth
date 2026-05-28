"""Hardware smoke test for the R6 connection.

Does NOT call --list-all-config (that has been known to brick the R6
viewfinder). Just:

  1. Kills ptpcamerad in a tight loop while autodetecting (to win the race).
  2. Inits and applies the same startup config the app would.
  3. Grabs 30 preview frames, reports fps + frame dimensions.
  4. Triggers a single full capture and times it; saves the JPEG to
     output/raw/probe_<timestamp>.jpg.
  5. Cleanly exits.

Run: `python tools/probe_camera.py`. Safe to re-run.
"""

from __future__ import annotations

import datetime as dt
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np

try:
    import gphoto2 as gp
except ImportError:
    print("python-gphoto2 not installed (pip install gphoto2)", file=sys.stderr)
    sys.exit(2)

OUT = Path(__file__).resolve().parent.parent / "output" / "raw"
OUT.mkdir(parents=True, exist_ok=True)

PREVIEW_FRAMES = 30
STARTUP_CONFIG = [
    ("imageformat", "L"),         # R6 short code = Large fine JPEG
    ("capturetarget", "Internal RAM"),
    ("autopoweroff", "0"),
]


def kill_ptpcamerad() -> None:
    subprocess.run(["killall", "-9", "ptpcamerad"], capture_output=True, check=False)


def autodetect_with_kill_loop(timeout_s: float = 6.0) -> list[tuple[str, str]]:
    """Hammer ptpcamerad while polling autodetect — wins most race conditions."""
    deadline = time.monotonic() + timeout_s
    last_attempt = 0
    while time.monotonic() < deadline:
        kill_ptpcamerad()
        try:
            cams = list(gp.Camera.autodetect())
        except Exception:
            cams = []
        if cams:
            return cams
        last_attempt += 1
        time.sleep(0.1)
    return []


def main() -> int:
    print(f"→ autodetect + kill-loop (up to 6s)…")
    detected = autodetect_with_kill_loop(6.0)
    if not detected:
        print(
            "  no camera detected after kill-loop.\n"
            "  - is the R6 powered on with the LCD showing a 'PC' icon?\n"
            "  - try `sudo killall -9 ptpcamerad` in another terminal, then re-run.\n"
            "  - if still failing: unplug USB, wait 5s, replug, re-run within 3s.",
            file=sys.stderr,
        )
        return 1
    name, port = detected[0]
    print(f"  found: {name} on {port}")

    print("→ initializing camera…")
    cam = gp.Camera()
    for attempt in range(3):
        try:
            cam.init()
            break
        except Exception as e:
            print(f"  init attempt {attempt + 1} failed: {e}")
            kill_ptpcamerad()
            time.sleep(0.3)
    else:
        print("  init failed after 3 attempts.", file=sys.stderr)
        return 1
    print("  connected.")

    print("→ applying startup config…")
    for key, val in STARTUP_CONFIG:
        try:
            config = cam.get_config()
            widget = config.get_child_by_name(key)
            widget.set_value(val)
            cam.set_single_config(key, widget)
            print(f"  set {key} = {val}")
        except Exception as e:
            print(f"  could not set {key} ({e}) — continuing")

    print(f"→ pulling {PREVIEW_FRAMES} preview frames…")
    t0 = time.monotonic()
    sizes = []
    for i in range(PREVIEW_FRAMES):
        try:
            cf = cam.capture_preview()
            data = bytes(memoryview(cf.get_data_and_size()))
        except Exception as e:
            print(f"  capture_preview frame {i} failed: {e}", file=sys.stderr)
            cam.exit()
            return 1
        arr = np.frombuffer(data, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            print(f"  frame {i} did not decode", file=sys.stderr)
        else:
            sizes.append(img.shape)
    elapsed = time.monotonic() - t0
    fps = PREVIEW_FRAMES / elapsed if elapsed > 0 else 0
    h, w = sizes[-1][:2] if sizes else (0, 0)
    print(f"  {PREVIEW_FRAMES} frames in {elapsed:.2f}s → {fps:.1f} fps")
    print(f"  preview resolution: {w} × {h}")
    if fps < 12:
        print("  WARNING: <12 fps. Check USB cable, USB hub, ptpcamerad.")

    print("→ triggering full capture…")
    t0 = time.monotonic()
    try:
        cam_path = cam.capture(gp.GP_CAPTURE_IMAGE)
        cf = cam.file_get(cam_path.folder, cam_path.name, gp.GP_FILE_TYPE_NORMAL)
        jpeg = bytes(memoryview(cf.get_data_and_size()))
    except Exception as e:
        print(f"  capture failed: {e}", file=sys.stderr)
        cam.exit()
        return 1
    cap_elapsed = time.monotonic() - t0
    print(f"  captured in {cap_elapsed:.2f}s ({len(jpeg)/1_000_000:.1f} MB)")

    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = OUT / f"probe_{ts}.jpg"
    out_path.write_bytes(jpeg)
    print(f"  saved → {out_path}")

    print("→ exiting camera cleanly")
    cam.exit()

    print()
    print("== SUMMARY ==")
    print(f"  fps:                 {fps:.1f}  ({'OK' if fps >= 15 else 'low' if fps >= 12 else 'BAD'})")
    print(f"  preview resolution:  {w} × {h}")
    print(f"  capture latency:     {cap_elapsed:.2f}s  ({'OK' if cap_elapsed <= 3 else 'slow' if cap_elapsed <= 5 else 'BAD'})")
    print(f"  capture size:        {len(jpeg)/1_000_000:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
