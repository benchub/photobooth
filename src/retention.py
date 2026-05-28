"""Output-directory retention: keep N most recent per subdir, prune the rest."""

from __future__ import annotations

import logging
from pathlib import Path

LOG = logging.getLogger(__name__)


def prune_directory(directory: Path, keep: int) -> int:
    """Delete files in `directory` beyond the `keep` newest by mtime.

    Returns the number of files deleted. Subdirectories are left alone.
    Sidecar `.meta.json` files are removed alongside their primary asset.
    """
    if not directory.exists():
        return 0
    candidates = [p for p in directory.iterdir() if p.is_file() and not p.name.endswith(".meta.json")]
    if len(candidates) <= keep:
        return 0
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    deleted = 0
    for p in candidates[keep:]:
        try:
            p.unlink(missing_ok=True)
            sidecar = p.with_suffix(p.suffix + ".meta.json")
            sidecar.unlink(missing_ok=True)
            deleted += 1
        except OSError as e:
            LOG.warning("could not prune %s: %s", p, e)
    return deleted
