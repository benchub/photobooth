"""Confirm autofocusdrive widget is available on the R6 and triggering AF works."""

from __future__ import annotations

import subprocess
import sys
import time

import gphoto2 as gp


def kill_ptpcamerad() -> None:
    subprocess.run(["killall", "-9", "ptpcamerad"], capture_output=True, check=False)


def autodetect_loop(timeout: float = 6.0):
    deadline = time.monotonic() + timeout
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
    cams = autodetect_loop()
    if not cams:
        print("no camera", file=sys.stderr); return 1

    cam = gp.Camera()
    for _ in range(3):
        try:
            cam.init(); break
        except Exception:
            kill_ptpcamerad(); time.sleep(0.3)
    else:
        print("init failed", file=sys.stderr); return 1

    config = cam.get_config()
    for name in ("autofocusdrive", "viewfinder", "manualfocusdrive"):
        try:
            w = config.get_child_by_name(name)
            print(f"{name}: type={w.get_type()} value={w.get_value()}")
        except Exception as e:
            print(f"{name}: <missing> ({e})")

    print("\ntriggering autofocusdrive=1 and waiting 0.5s…")
    try:
        w = config.get_child_by_name("autofocusdrive")
        w.set_value(1)
        cam.set_single_config("autofocusdrive", w)
        time.sleep(0.5)
        print("  succeeded (camera should have focused now)")
    except Exception as e:
        print(f"  failed: {e}")

    cam.exit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
