"""Run key_final on the most-recent raw capture, twice:
once with guided_filter on, once with it off. Save both for comparison
and report pixel statistics so we can tell what's producing black output.
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.chroma import ChromaKeyer
from src.compositor import composite, save_jpeg

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "output" / "raw"
OUT_DIR = ROOT / "output" / "composite-debug"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def stats(name: str, img: np.ndarray) -> None:
    print(f"  {name}: shape={img.shape} dtype={img.dtype} "
          f"min={img.min()} max={img.max()} mean={img.mean():.1f}")


def main() -> int:
    raws = sorted(p for p in RAW_DIR.glob("*.jpg")
                  if not p.name.startswith("probe_"))
    if not raws:
        print("no raw captures found", file=sys.stderr)
        return 1
    raw_path = raws[-1]
    bg_path = next(iter(sorted((ROOT / "backgrounds").glob("sample_*.jpg"))))
    print(f"raw: {raw_path.name}")
    print(f"bg:  {bg_path.name}")

    raw_bgr = cv2.imread(str(raw_path))
    print()
    stats("raw bgr", raw_bgr)

    for use_gf in (True, False):
        print()
        print(f"-- key_final guided_filter={use_gf}")
        keyer = ChromaKeyer(guided_filter=use_gf)
        out = composite(raw_bgr, bg_path, keyer)
        arr = np.array(out)
        stats(f"composite (gf={use_gf})", arr)
        out_path = OUT_DIR / f"{raw_path.stem}_gf{int(use_gf)}.jpg"
        save_jpeg(out, out_path, quality=88)
        print(f"  wrote {out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
