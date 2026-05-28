"""Background picker grid.

Scans `backgrounds/` for jpg/png files, builds a grid of thumbnails, lets
the user navigate with arrow keys / mouse and commit with Enter or click.

Selection moves the highlighted border. The picker tracks `selected_path`;
BoothWindow reads that when transitioning to LIVE_PREVIEW.

Thumbnails are generated in-memory on first scan — at 6–20 backgrounds and
PIL's fast JPEG decode, the cost is small enough to skip disk caching.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..config import Config
from .scale import scale_px, short_side

THUMB_ASPECT = 3 / 2          # matches camera aspect
PAD = 8                       # gap between image and frame edge (px)
TARGET_THUMB_FRAC = 0.34      # ideal thumb width as a fraction of the short side
SUPPORTED_EXTS = {".jpg", ".jpeg", ".png"}


class _Thumb(QFrame):
    """One clickable thumbnail. Border thickens when selected."""

    clicked = pyqtSignal(int)  # emits index

    def __init__(
        self, index: int, pixmap: QPixmap, w: int, h: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.index = index
        self.setFixedSize(w + 2 * PAD, h + 2 * PAD)
        self._image = QLabel(self)
        self._image.setPixmap(pixmap)
        self._image.setGeometry(PAD, PAD, w, h)
        self.set_selected(False)

    def set_selected(self, on: bool) -> None:
        if on:
            self.setStyleSheet(
                "border: 6px solid #4af; border-radius: 8px; background: #222;"
            )
        else:
            self.setStyleSheet(
                "border: 2px solid #444; border-radius: 8px; background: #222;"
            )

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.index)


class BackgroundPicker(QWidget):
    background_selected = pyqtSignal(object)  # emits Path

    def __init__(self, cfg: Config, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self._paths: list[Path] = []
        self._thumbs: list[_Thumb] = []
        self._selected_index = 0
        self._thumb_size: tuple[int, int] = (0, 0)

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(40, 40, 40, 40)

        self._title = QLabel("Pick a background", self)
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._layout.addWidget(self._title)

        self._hint = QLabel("← → ↑ ↓ to move · Enter to choose", self)
        self._hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._layout.addWidget(self._hint)

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setStyleSheet("background: transparent; border: none;")
        self._grid_holder = QWidget(self._scroll)
        self._grid = QGridLayout(self._grid_holder)
        self._grid.setSpacing(20)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._scroll.setWidget(self._grid_holder)
        self._layout.addWidget(self._scroll, 1)

        self._apply_responsive_styles()
        self._build_grid()

    def on_enter(self) -> None:
        # Pick up any new files dropped into backgrounds/ since last visit.
        self._build_grid()

    def _grid_cols(self) -> int:
        """Column count chosen from the available width and a target thumb
        size, so a wide screen gets more columns and a tall one fewer —
        no orientation baked in. Never more columns than backgrounds."""
        s = short_side(self)
        margin = scale_px(40, s, minimum=8)
        spacing = scale_px(20, s, minimum=4)
        avail = max(1, self.width() - 2 * margin)
        target = max(160, int(s * TARGET_THUMB_FRAC))
        cols = max(1, (avail + spacing) // (target + spacing))
        if self._paths:
            cols = min(cols, len(self._paths))
        return int(cols)

    def _thumb_dims(self) -> tuple[int, int]:
        """Thumbnail (w, h) sized to fill a row at the current column count."""
        s = short_side(self)
        margin = scale_px(40, s, minimum=8)
        spacing = scale_px(20, s, minimum=4)
        cols = self._grid_cols()
        avail = self.width() - 2 * margin - (cols - 1) * spacing
        cell_w = max(120, avail // cols)
        tw = cell_w - 2 * PAD
        th = int(tw / THUMB_ASPECT)
        return tw, th

    def _apply_responsive_styles(self) -> None:
        s = short_side(self)
        self._layout.setContentsMargins(*([scale_px(40, s, minimum=8)] * 4))
        self._grid.setSpacing(scale_px(20, s, minimum=4))
        self._title.setStyleSheet(
            f"color: #eee; font-size: {scale_px(48, s, minimum=18)}px;"
            " font-weight: 700;"
        )
        self._hint.setStyleSheet(
            f"color: #888; font-size: {scale_px(20, s, minimum=11)}px;"
        )

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._apply_responsive_styles()
        if self._thumb_dims() != self._thumb_size:
            # Orientation/size class changed — re-render thumbnails to match.
            self._rebuild_thumbs()

    def _scan(self) -> list[Path]:
        d = self.cfg.backgrounds_dir
        if not d.exists():
            return []
        return sorted(
            p for p in d.iterdir()
            if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS
        )

    def _build_grid(self) -> None:
        new_paths = self._scan()
        if new_paths == self._paths and self._thumbs:
            return
        self._paths = new_paths
        self._rebuild_thumbs()

    def _rebuild_thumbs(self) -> None:
        """(Re)render every thumbnail at the current dynamic size."""
        tw, th = self._thumb_dims()
        self._thumb_size = (tw, th)
        cols = self._grid_cols()
        # Clear any existing thumbs.
        for t in self._thumbs:
            t.setParent(None)
            t.deleteLater()
        self._thumbs = []

        for i, path in enumerate(self._paths):
            pix = self._load_thumb(path, tw, th)
            t = _Thumb(i, pix, tw, th, self._grid_holder)
            t.clicked.connect(self._on_thumb_clicked)
            row, col = divmod(i, cols)
            self._grid.addWidget(t, row, col)
            self._thumbs.append(t)

        if self._paths:
            self._selected_index = min(self._selected_index, len(self._paths) - 1)
            self._refresh_highlight()

    def _load_thumb(self, path: Path, w: int, h: int) -> QPixmap:
        img = QImage(str(path))
        if img.isNull():
            # Render a placeholder so a broken file doesn't crash the grid.
            img = QImage(w, h, QImage.Format.Format_RGB32)
            img.fill(0x404040)
        return QPixmap.fromImage(img.scaled(
            QSize(w, h),
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )).copy(0, 0, w, h)

    def _refresh_highlight(self) -> None:
        for i, t in enumerate(self._thumbs):
            t.set_selected(i == self._selected_index)

    def _on_thumb_clicked(self, index: int) -> None:
        self._selected_index = index
        self._refresh_highlight()

    def handle_arrow(self, key: int) -> None:
        if not self._paths:
            return
        n = len(self._paths)
        cols = self._grid_cols()
        i = self._selected_index
        if key == Qt.Key.Key_Left:
            i = (i - 1) % n
        elif key == Qt.Key.Key_Right:
            i = (i + 1) % n
        elif key == Qt.Key.Key_Up:
            i = max(0, i - cols)
        elif key == Qt.Key.Key_Down:
            i = min(n - 1, i + cols)
        self._selected_index = i
        self._refresh_highlight()
        if self._thumbs:
            self._scroll.ensureWidgetVisible(self._thumbs[i])

    @property
    def selected_path(self) -> Path | None:
        if not self._paths:
            return None
        return self._paths[self._selected_index]

    def commit_selection(self) -> None:
        """Called by BoothWindow on Enter — fires the signal."""
        if self.selected_path is not None:
            self.background_selected.emit(self.selected_path)
