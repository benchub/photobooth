"""Logging to ~/.photobooth/log.txt plus stderr."""

from __future__ import annotations

import logging
import sys
from pathlib import Path


def setup(level: int = logging.INFO) -> Path:
    log_dir = Path.home() / ".photobooth"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        log_dir = Path("/tmp")
    log_path = log_dir / "log.txt"

    handlers: list[logging.Handler] = []
    try:
        fh = logging.FileHandler(log_path, mode="a", encoding="utf-8")
        fh.setLevel(level)
        handlers.append(fh)
    except OSError:
        pass

    sh = logging.StreamHandler(sys.stderr)
    sh.setLevel(level)
    handlers.append(sh)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    for h in handlers:
        h.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(level)
    # Replace any existing handlers (e.g. on re-launch).
    for h in list(root.handlers):
        root.removeHandler(h)
    for h in handlers:
        root.addHandler(h)

    return log_path
