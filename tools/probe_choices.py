"""Enumerate the choice strings for `imageformat`, the AF area/subject-detection
keys, and a few related keys — without triggering --list-all-config (which is
known to brick the R6).

Use the AF section to find the value that puts your body into a face-detecting
mode, then set camera.af_method_value (and friends) in config.yaml.
"""

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


def show_widget(config, name: str) -> None:
    try:
        w = config.get_child_by_name(name)
    except Exception as e:
        print(f"  {name}: <not present> ({e})")
        return
    try:
        current = w.get_value()
    except Exception:
        current = "?"
    print(f"  {name} (current: {current!r}):")
    try:
        n = w.count_choices()
    except Exception:
        # Not a multiple-choice widget.
        return
    for i in range(n):
        print(f"    [{i}] {w.get_choice(i)!r}")


def main() -> int:
    cams = autodetect_loop()
    if not cams:
        print("no camera", file=sys.stderr)
        return 1
    print(f"camera: {cams[0][0]}")

    cam = gp.Camera()
    for _ in range(3):
        try:
            cam.init(); break
        except Exception:
            kill_ptpcamerad(); time.sleep(0.3)
    else:
        print("init failed", file=sys.stderr); return 1

    config = cam.get_config()
    print("\n-- capture / format --")
    for key in ("imageformat", "imageformatsd", "imageformatcf",
                "capturetarget", "autopoweroff"):
        show_widget(config, key)

    # AF area mode + subject/eye detection. Names vary by body/firmware; most of
    # these won't exist — the ones that do, and their current value + choices,
    # tell you what to put in camera.af_method_value etc. Look for a choice
    # like "Face+Tracking" / "Whole Area" / "u-Live" to get face priority.
    print("\n-- autofocus area / subject detection --")
    for key in ("afmethod", "eosafmethod", "liveviewafmethod", "afarea",
                "focusmode", "focusmode2", "continuousaf", "servoaf",
                "eosmovieservoaf", "aimode", "eyedetection", "eyeaf",
                "subjectdetection", "subjecttodetect", "trackingaf",
                "wholeareaaf"):
        show_widget(config, key)

    cam.exit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
