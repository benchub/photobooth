"""Read the camera battery level over USB, with an optional low-battery watch.

The R6 reports its charge through the gphoto2 `batterylevel` config key (a
string like ``"75%"``, sometimes a word like ``"Low"`` on other bodies). This
reads it the same way tools/probe_choices.py reads other config keys.

  # one-shot reading
  python tools/probe_battery.py

  # poll every 60s and pop a macOS alert when it drops to <=25%
  python tools/probe_battery.py --watch 60 --threshold 25

IMPORTANT: only one process can hold the camera's USB connection at a time.
The booth app (src/camera.py) keeps that connection while it runs, so DO NOT
run this alongside a live booth — it won't be able to grab the camera. Use it
when the booth is idle, or for pre-event checks. For alerts *during* an event,
the monitoring has to live inside the running app.

Run: `python tools/probe_battery.py`. Safe to re-run.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time

try:
    import gphoto2 as gp
    _GPHOTO_AVAILABLE = True
except ImportError:
    gp = None  # type: ignore
    _GPHOTO_AVAILABLE = False

BATTERY_KEY = "batterylevel"
# Words some bodies report instead of a percentage, lowest first.
WORD_LEVELS = {"empty": 0, "low": 15, "half": 50, "normal": 75, "full": 100}


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


def connect():
    """Return an initialized gp.Camera, or None on failure."""
    cams = autodetect_loop()
    if not cams:
        print("no camera detected (is it on, in PC mode, USB connected?)", file=sys.stderr)
        return None
    cam = gp.Camera()
    for _ in range(3):
        try:
            cam.init()
            return cam
        except Exception:
            kill_ptpcamerad()
            time.sleep(0.3)
    print("camera init failed after 3 attempts", file=sys.stderr)
    return None


def read_battery(cam) -> tuple[str, int | None]:
    """Return (raw_string, percent_or_None) for the camera's battery level."""
    config = cam.get_config()
    try:
        widget = config.get_child_by_name(BATTERY_KEY)
    except Exception as e:
        raise RuntimeError(f"camera does not expose {BATTERY_KEY!r}: {e}") from e
    raw = str(widget.get_value())
    return raw, parse_percent(raw)


def parse_percent(raw: str) -> int | None:
    """Best-effort: '75%' -> 75, 'Low' -> 15, unknown -> None."""
    m = re.search(r"(\d+)\s*%?", raw)
    if m:
        return max(0, min(100, int(m.group(1))))
    return WORD_LEVELS.get(raw.strip().lower())


def macos_notify(title: str, message: str) -> None:
    """Pop a macOS Notification Center banner with a sound. No-op elsewhere."""
    if sys.platform != "darwin":
        return
    text = message.replace('"', "'")
    title = title.replace('"', "'")
    script = f'display notification "{text}" with title "{title}" sound name "Basso"'
    subprocess.run(["osascript", "-e", script], capture_output=True, check=False)


def fmt(raw: str, pct: int | None) -> str:
    return f"{pct}%" if pct is not None else f"{raw!r} (could not parse a percentage)"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--watch",
        type=float,
        metavar="SECONDS",
        help="Poll continuously every SECONDS instead of reading once.",
    )
    p.add_argument(
        "--threshold",
        type=int,
        default=25,
        help="Low-battery alert threshold in %% (default 25). Used with --watch.",
    )
    args = p.parse_args(argv)

    if not _GPHOTO_AVAILABLE:
        print("python-gphoto2 not installed (pip install gphoto2)", file=sys.stderr)
        return 2

    cam = connect()
    if cam is None:
        return 1

    try:
        if args.watch is None:
            raw, pct = read_battery(cam)
            print(f"battery: {fmt(raw, pct)}")
            return 0

        print(
            f"watching battery every {args.watch:g}s; "
            f"alerting at <={args.threshold}%. Ctrl-C to stop."
        )
        alerted = False  # latch so we alert once per crossing, not every poll
        while True:
            try:
                raw, pct = read_battery(cam)
            except Exception as e:
                print(f"  read failed: {e}", file=sys.stderr)
                pct = None
                raw = "?"
            stamp = time.strftime("%H:%M:%S")
            low = pct is not None and pct <= args.threshold
            flag = "  <-- LOW" if low else ""
            print(f"  [{stamp}] battery: {fmt(raw, pct)}{flag}", flush=True)
            if low and not alerted:
                macos_notify(
                    "Photobooth: camera battery low",
                    f"Battery at {pct}% (threshold {args.threshold}%). Swap soon.",
                )
                alerted = True
            elif pct is not None and pct > args.threshold:
                alerted = False  # re-arm after a battery swap
            time.sleep(args.watch)
    except KeyboardInterrupt:
        print("\nstopped.")
        return 0
    finally:
        cam.exit()


if __name__ == "__main__":
    sys.exit(main())
