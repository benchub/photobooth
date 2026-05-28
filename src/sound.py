"""Tiny sound-effect helper. Wraps QSoundEffect for fire-and-forget playback.

Falls back to silent no-ops if QtMultimedia or the WAV file is missing.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)


class SoundEffect:
    """Wrap a single WAV file. Calls .play() are non-blocking.

    Set `loop=True` to play continuously until .stop() is called.
    """

    def __init__(
        self,
        path: Path,
        volume: float = 0.8,
        enabled: bool = True,
        loop: bool = False,
    ) -> None:
        self._effect: Any = None
        self._enabled = enabled
        if not enabled or not path.exists():
            return
        try:
            from PyQt6.QtCore import QUrl
            from PyQt6.QtMultimedia import QSoundEffect
        except ImportError as e:
            LOG.warning("QtMultimedia unavailable; sounds disabled (%s)", e)
            return

        self._effect = QSoundEffect()
        self._effect.setSource(QUrl.fromLocalFile(str(path)))
        self._effect.setVolume(max(0.0, min(1.0, volume)))
        if loop:
            self._effect.setLoopCount(QSoundEffect.Loop.Infinite.value)

    def play(self) -> None:
        if self._effect is not None:
            self._effect.play()

    def stop(self) -> None:
        if self._effect is not None:
            self._effect.stop()

    @property
    def enabled(self) -> bool:
        return self._effect is not None
