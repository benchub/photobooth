"""Live composited preview.

Receives BGR frames from the camera worker via `update_frame()`, runs them
through `ChromaKeyer.key_preview()` against the chosen background, and
paints the result.

Until the camera worker (M3) is wired in, the widget shows the chosen
background as a static placeholder.
"""

from __future__ import annotations

import time
from pathlib import Path

import cv2
import numpy as np
from PyQt6.QtCore import Qt, QTimer, pyqtSlot
from PyQt6.QtGui import QImage, QPixmap, QResizeEvent
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ..chroma import ChromaKeyer
from ..config import Config
from .scale import scale_px, short_side


class PreviewWidget(QWidget):
    def __init__(self, cfg: Config, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.cfg = cfg

        # Fallback local keyer; replaced by BoothWindow's shared keyer on
        # first frame via `_resolve_keyer()`. The shared instance lets the
        # settings overlay tune values and have the change show up live.
        c = cfg.chroma
        self._keyer = ChromaKeyer(
            hue_low=c.hue_low,
            hue_high=c.hue_high,
            sat_min=c.sat_min,
            val_min=c.val_min,
            feather_px_preview=c.feather_px_preview,
            feather_px_final=c.feather_px_final,
            spill_suppress=c.spill_suppress,
            guided_filter=c.guided_filter,
        )
        self._resolved_keyer: ChromaKeyer | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._display = QLabel(self)
        self._display.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._display.setStyleSheet("background-color: #222;")
        layout.addWidget(self._display)

        self._hint = QLabel("Press SPACE to take photos!", self._display)
        self._hint.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._fps_label = QLabel("", self._display)

        self._camera_warning = QLabel("Camera not ready", self._display)
        self._camera_warning.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._camera_warning.hide()

        self._apply_responsive_styles()

        self._bg_bgr: np.ndarray | None = None
        self._last_frame_time: float | None = None
        self._fps_ema = 0.0

        self._no_frames_timer = QTimer(self)
        self._no_frames_timer.timeout.connect(self._check_for_stale_camera)
        self._no_frames_timer.start(1000)

        self._camera_disconnected_reason: str | None = None

    def set_camera_status(self, connected: bool, reason: str | None) -> None:
        """Called by BoothWindow when the camera connects/disconnects."""
        if connected:
            self._camera_disconnected_reason = None
            self._camera_warning.hide()
        else:
            self._camera_disconnected_reason = reason
            self._camera_warning.setText(f"Camera not ready:\n{reason or '…'}")
            self._camera_warning.adjustSize()
            self._reposition_overlays()
            self._camera_warning.show()
            self._camera_warning.raise_()

    def on_enter(self) -> None:
        parent = self.parentWidget()
        while parent is not None and not hasattr(parent, "current_background"):
            parent = parent.parentWidget()
        bg_path: Path | None = getattr(parent, "current_background", None) if parent else None
        self._bg_bgr = _load_background(bg_path) if bg_path else None
        if self._bg_bgr is not None:
            self._show_image(self._bg_bgr)
        else:
            self._display.setPixmap(QPixmap())
        self._reposition_overlays()

    @pyqtSlot(np.ndarray)
    def update_frame(self, frame_bgr: np.ndarray) -> None:
        """Called by CameraWorker on every preview frame.

        The camera worker keeps live view active continuously so the R6's
        Servo AF tracks focus even while we're not showing the preview
        (during countdown/review/etc.) — we just don't run the chroma key
        or paint when we're hidden.
        """
        now = time.monotonic()
        if self._last_frame_time is not None:
            dt = now - self._last_frame_time
            if dt > 0 and self.isVisible():
                inst_fps = 1.0 / dt
                self._fps_ema = (
                    0.2 * inst_fps + 0.8 * self._fps_ema if self._fps_ema else inst_fps
                )
                self._fps_label.setText(f"{self._fps_ema:4.1f} fps")
        self._last_frame_time = now

        if not self.isVisible():
            return

        keyer = self._resolve_keyer()
        if self._bg_bgr is not None:
            composited = keyer.key_preview(frame_bgr, self._bg_bgr)
        else:
            composited = frame_bgr
        self._show_image(composited)

    def _resolve_keyer(self) -> ChromaKeyer:
        if self._resolved_keyer is not None:
            return self._resolved_keyer
        parent = self.parentWidget()
        while parent is not None and not hasattr(parent, "_keyer"):
            parent = parent.parentWidget()
        if parent is not None and isinstance(getattr(parent, "_keyer", None), ChromaKeyer):
            self._resolved_keyer = parent._keyer
        else:
            self._resolved_keyer = self._keyer
        return self._resolved_keyer

    def _show_image(self, bgr: np.ndarray) -> None:
        h, w = bgr.shape[:2]
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        qimg = QImage(rgb.data, w, h, w * 3, QImage.Format.Format_RGB888).copy()
        pix = QPixmap.fromImage(qimg).scaled(
            self._display.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._display.setPixmap(pix)

    def _apply_responsive_styles(self) -> None:
        s = short_side(self)
        hint_pad_v = scale_px(14, s, minimum=4)
        hint_pad_h = scale_px(28, s, minimum=8)
        self._hint.setStyleSheet(
            f"color: #fff; font-size: {scale_px(36, s, minimum=14)}px;"
            f" font-weight: 700; background: rgba(0,0,0,0.55);"
            f" padding: {hint_pad_v}px {hint_pad_h}px; border-radius: 8px;"
        )
        self._fps_label.setStyleSheet(
            f"color: #fff; font-size: {scale_px(14, s, minimum=9)}px;"
            " background: rgba(0,0,0,0.5); padding: 4px 8px; border-radius: 4px;"
        )
        warn_pad_v = scale_px(10, s, minimum=4)
        warn_pad_h = scale_px(22, s, minimum=8)
        self._camera_warning.setStyleSheet(
            f"color: #fff; font-size: {scale_px(22, s, minimum=12)}px;"
            f" font-weight: 700; background: rgba(180,40,40,0.85);"
            f" padding: {warn_pad_v}px {warn_pad_h}px; border-radius: 6px;"
        )

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._apply_responsive_styles()
        if self._bg_bgr is not None and self._last_frame_time is None:
            # No live frames yet — re-render the background placeholder.
            self._show_image(self._bg_bgr)
        self._reposition_overlays()

    def _reposition_overlays(self) -> None:
        self._hint.adjustSize()
        self._fps_label.adjustSize()
        self._camera_warning.adjustSize()
        w, h = self._display.width(), self._display.height()
        self._hint.move((w - self._hint.width()) // 2, h - self._hint.height() - 40)
        self._fps_label.move(w - self._fps_label.width() - 12, 12)
        self._camera_warning.move((w - self._camera_warning.width()) // 2, 30)

    def _check_for_stale_camera(self) -> None:
        import time as _t
        timeout = self.cfg.ui.no_frames_timeout_s
        if self._last_frame_time is None:
            stale = True
        else:
            stale = (_t.monotonic() - self._last_frame_time) > timeout
        if stale and self.isVisible():
            self._camera_warning.show()
            self._camera_warning.raise_()
        else:
            self._camera_warning.hide()


def _load_background(path: Path) -> np.ndarray | None:
    """Load BGR uint8 image from disk; return None on failure."""
    img = cv2.imread(str(path))
    return img if img is not None else None
