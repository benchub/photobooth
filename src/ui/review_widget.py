"""Review screen — paints the 3 composites in a horizontal band.

Custom paintEvent: photos are drawn at their actual 3:2 aspect, sized so
the trio fills the screen width comfortably, with white frames + warm
sepia background. No wasted vertical strips inside cells.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QPainter, QPixmap
from PyQt6.QtWidgets import QWidget

from ..config import Config
from .scale import scale_px, short_side

BG = QColor(26, 20, 16)              # warm dark backdrop
FRAME = QColor(245, 232, 200)        # cream frame
TITLE_COLOR = QColor(245, 232, 200)
CAPTION_COLOR = QColor(196, 181, 154)


class ReviewWidget(QWidget):
    def __init__(self, cfg: Config, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.setAutoFillBackground(True)
        pal = self.palette()
        pal.setColor(self.backgroundRole(), BG)
        self.setPalette(pal)

        self._pixmaps: list[QPixmap | None] = [None, None, None]
        self._caption = "Uploading in a moment…"

    def on_enter(self) -> None:
        parent = self.parentWidget()
        while parent is not None and not hasattr(parent, "_session_composites"):
            parent = parent.parentWidget()
        composites: list[Path] = list(getattr(parent, "_session_composites", []))[:3]

        self._pixmaps = [None, None, None]
        for i, path in enumerate(composites):
            if i >= 3:
                break
            if path and Path(path).exists():
                pix = QPixmap(str(path))
                self._pixmaps[i] = pix if not pix.isNull() else None
        self.update()

    def set_caption(self, text: str) -> None:
        self._caption = text
        self.update()

    def photo_layout(self) -> dict:
        """Geometry for the three photo frames at the current size.

        The composites are 3:2 *landscape* photos; they're laid out along the
        screen's long axis (a row on landscape, a column on portrait) so each
        is as large as possible, with the frame hugging the photo's own
        aspect — never stretched to fill a cell. Exposed (not private) so the
        layout can be asserted in tests without rendering.
        """
        w, h = self.width(), self.height()
        s = short_side(self)
        margin = scale_px(30, s, minimum=8)
        title_h = scale_px(96, s, minimum=44)
        cap_h = scale_px(48, s, minimum=28)
        gap = scale_px(24, s, minimum=6)
        frame_px = scale_px(10, s, minimum=3)
        photo_aspect = 3 / 2
        n = 3

        band_top = margin + title_h + scale_px(20, s, minimum=6)
        band_bottom = h - cap_h - margin
        band_h = max(60, band_bottom - band_top)
        band_w = w - 2 * margin

        horizontal = w >= h
        if horizontal:
            avail_w = (band_w - (n - 1) * gap) // n - 2 * frame_px
            photo_w = min(avail_w, int((band_h - 2 * frame_px) * photo_aspect))
        else:
            avail_h = (band_h - (n - 1) * gap) // n - 2 * frame_px
            photo_w = min(band_w - 2 * frame_px, int(avail_h * photo_aspect))
        photo_w = max(40, photo_w)
        photo_h = int(photo_w / photo_aspect)
        cell_w = photo_w + 2 * frame_px
        cell_h = photo_h + 2 * frame_px

        if horizontal:
            total_w = cell_w * n + gap * (n - 1)
            start_x = (w - total_w) // 2
            start_y = band_top + (band_h - cell_h) // 2
            cells = [(start_x + i * (cell_w + gap), start_y) for i in range(n)]
        else:
            total_h = cell_h * n + gap * (n - 1)
            start_x = (w - cell_w) // 2
            start_y = band_top + (band_h - total_h) // 2
            cells = [(start_x, start_y + i * (cell_h + gap)) for i in range(n)]

        return {
            "horizontal": horizontal, "cells": cells,
            "photo_w": photo_w, "photo_h": photo_h,
            "cell_w": cell_w, "cell_h": cell_h, "frame_px": frame_px,
        }

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        p = QPainter(self)
        p.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
        )

        w, h = self.width(), self.height()
        s = short_side(self)
        p.fillRect(0, 0, w, h, QBrush(BG))

        margin = scale_px(30, s, minimum=8)

        # --- Title ---
        title_font = QFont()
        title_font.setStyleHint(QFont.StyleHint.Serif)
        title_font.setPointSize(scale_px(48, s, minimum=22))
        title_font.setWeight(QFont.Weight.Bold)
        p.setFont(title_font)
        p.setPen(TITLE_COLOR)
        title_h = scale_px(96, s, minimum=44)
        p.drawText(QRectF(0, margin, w, title_h),
                   int(Qt.AlignmentFlag.AlignCenter), "Great shots!")

        # --- Caption (rendered later but allocate space) ---
        caption_font = QFont()
        caption_font.setPointSize(scale_px(22, s, minimum=14))
        cap_h = scale_px(48, s, minimum=28)

        # --- Photo layout (row on landscape, column on portrait) ---
        lay = self.photo_layout()
        cells = lay["cells"]
        photo_w, photo_h = lay["photo_w"], lay["photo_h"]
        cell_w, cell_h, frame_px = lay["cell_w"], lay["cell_h"], lay["frame_px"]

        for i, (cell_x, cell_y) in enumerate(cells):
            # Frame hugs the photo.
            p.setBrush(QBrush(FRAME))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(cell_x, cell_y, cell_w, cell_h, 6, 6)
            inner_x = cell_x + frame_px
            inner_y = cell_y + frame_px

            pix = self._pixmaps[i]
            if pix is None or pix.isNull():
                p.fillRect(inner_x, inner_y, photo_w, photo_h, QColor(42, 34, 24))
                continue
            scaled = pix.scaled(
                photo_w, photo_h,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            # Center-crop the over-scaled pixmap into the inner rect.
            sx = max(0, (scaled.width() - photo_w) // 2)
            sy = max(0, (scaled.height() - photo_h) // 2)
            p.drawPixmap(
                inner_x, inner_y, photo_w, photo_h,
                scaled, sx, sy, photo_w, photo_h,
            )

        # --- Caption ---
        p.setFont(caption_font)
        p.setPen(CAPTION_COLOR)
        p.drawText(
            QRectF(0, h - cap_h - scale_px(20, s, minimum=6), w, cap_h),
            int(Qt.AlignmentFlag.AlignCenter),
            self._caption,
        )

        p.end()
