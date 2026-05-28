"""Placeholder state widgets for milestones not yet implemented.

These get replaced in:
  AttractWidget       — M7 (Ken-Burns demo cycle)
  CaptureFlashWidget  — M5 (white flash overlay + shutter sound)
  ReviewWidget        — M5 (show 3 composites + strip)
  UploadingWidget     — M6 (progress + result)
  DoneWidget          — M5/M7 (thank-you screen)
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ..config import Config
from ..sound import SoundEffect
from .scale import scale_px, short_side


class _CenteredLabelWidget(QWidget):
    # `pixel_size` is the design size at DESIGN_HEIGHT; it's scaled to the
    # actual widget height in `_apply_style`.
    text: str = ""
    pixel_size: int = 64
    color: str = "#eee"
    background: str = "transparent"
    weight: int = 700

    def __init__(self, cfg: Config, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        # Without this, a plain QWidget ignores its own `background-color`
        # stylesheet and shows the main window's #111 cascade instead — which
        # turned the white SNAP flash into a dark-grey screen.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.label = QLabel(self.text, self)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setWordWrap(True)
        layout.addWidget(self.label)
        self._apply_style()

    def _apply_style(self) -> None:
        s = short_side(self)
        size = scale_px(self.pixel_size, s, minimum=16)
        spacing = scale_px(4, s, minimum=1)
        self.setStyleSheet(f"background-color: {self.background};")
        self.label.setStyleSheet(
            f"color: {self.color}; font-size: {size}px;"
            f" font-weight: {self.weight}; letter-spacing: {spacing}px;"
            " background: transparent;"
        )

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._apply_style()

    def on_enter(self) -> None:
        pass


# AttractWidget moved to its own module — see src/ui/attract_widget.py.


class CaptureFlashWidget(_CenteredLabelWidget):
    text = "SNAP!"
    pixel_size = 200
    color = "black"
    background = "white"
    weight = 900

    def __init__(self, cfg: Config, parent: QWidget | None = None) -> None:
        super().__init__(cfg, parent)
        self._shutter = SoundEffect(
            cfg.sounds_dir / "shutter.wav",
            volume=cfg.sound.volume,
            enabled=cfg.sound.enabled,
        )

    def on_enter(self) -> None:
        self._apply_style()
        self._shutter.play()


class UploadingWidget(_CenteredLabelWidget):
    text = "Uploading…"
    pixel_size = 72

    def set_status(self, status: str) -> None:
        self.label.setText(status)


class DoneWidget(_CenteredLabelWidget):
    text = "Thanks!\n\nYour photos are saved.\nPress SPACE for more."
    pixel_size = 56

    def set_message(self, msg: str) -> None:
        self.label.setText(msg)

    def reset_text(self) -> None:
        self.label.setText(self.text)
