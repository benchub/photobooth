"""Attract-mode screen.

Shows:
  - Big "PHOTOBOOTH" title and "Press SPACE to start" CTA
  - Rotating carousel of strips made so far (scanned from output/strips/)
  - "See your photos at <URL>" + QR code (from config)
  - Persistent upload status footer
"""

from __future__ import annotations

import io
import logging
import random
from pathlib import Path

import qrcode
from PyQt6.QtCore import QEasingCurve, QRectF, Qt, QTimer, QVariantAnimation
from PyQt6.QtGui import QBrush, QColor, QFont, QFontMetrics, QPainter, QPixmap
from PyQt6.QtWidgets import QBoxLayout, QLabel, QVBoxLayout, QWidget

from ..config import Config
from .scale import scale_px, short_side

LOG = logging.getLogger(__name__)

CAROUSEL_SLIDE_MS = 550          # duration of the slide-down transition
STRIPS_RESCAN_MS = 15000         # rescan output/strips/ for new files

FRAME_COLOR = QColor(245, 232, 200)   # cream
SCREEN_BG = QColor(14, 12, 10)        # #0e0c0a — the attract screen background
PANE_BG = QColor(26, 20, 16)          # warm dark (letterbox behind a photo)


class _FramedImage(QWidget):
    """Paints a single pixmap fit to its own aspect ratio and centered, with
    a cream frame that *hugs the image* — not the widget.

    This is the fix for the original portrait-only assumption: the frame's
    size is driven by the content, so a tall photo strip stays a tall framed
    strip whether the surrounding cell is wide (landscape) or tall (portrait),
    instead of stretching a border around an arbitrary container.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self._placeholder = ""
        self._bg = SCREEN_BG

        # Slide-transition state. When _progress < 1.0 the widget is mid-shuffle:
        # _outgoing slides down off the bottom while _incoming slides down into
        # place from the top. At rest (_progress == 1.0) we just draw _pixmap.
        self._progress = 1.0
        self._incoming: QPixmap | None = None
        self._outgoing: QPixmap | None = None
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(CAROUSEL_SLIDE_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._anim.valueChanged.connect(self._on_anim_value)
        self._anim.finished.connect(self._on_anim_finished)

    def set_pixmap(self, pix: QPixmap | None) -> None:
        """Swap the displayed strip immediately (no animation)."""
        self._anim.stop()
        self._incoming = None
        self._outgoing = None
        self._progress = 1.0
        self._pixmap = pix if (pix is not None and not pix.isNull()) else None
        self.update()

    def animate_to(self, pix: QPixmap | None) -> None:
        """Shuffle to `pix`: it slides in from the top while the current strip
        slides off the bottom. Falls back to an instant swap when there's
        nothing to slide from (first strip / placeholder) or while off-screen."""
        pix = pix if (pix is not None and not pix.isNull()) else None
        if pix is None or self._pixmap is None or not self.isVisible():
            self.set_pixmap(pix)
            return
        self._anim.stop()
        self._outgoing = self._pixmap
        self._incoming = pix
        self._pixmap = pix          # the settled image once the slide completes
        self._progress = 0.0
        self._anim.start()

    def _on_anim_value(self, value: object) -> None:
        self._progress = float(value)  # type: ignore[arg-type]
        self.update()

    def _on_anim_finished(self) -> None:
        self._incoming = None
        self._outgoing = None
        self._progress = 1.0
        self.update()

    def set_placeholder(self, text: str) -> None:
        self._placeholder = text
        self.update()

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        p = QPainter(self)
        p.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
        )
        # Fill the whole rect with the screen colour first — a custom-paint
        # QWidget otherwise leaves unpainted areas black, which read as a
        # black box sitting on the (lighter) screen background.
        p.fillRect(self.rect(), QBrush(self._bg))

        if self._progress < 1.0:
            # Mid-shuffle: both strips travel down by the widget height, one
            # widget-height apart, so it reads as a continuous downward scroll.
            travel = self.height()
            self._draw_strip(p, self._outgoing, int(self._progress * travel))
            self._draw_strip(p, self._incoming, int((self._progress - 1.0) * travel))
        else:
            self._draw_strip(p, self._pixmap, 0)
        p.end()

    def _draw_strip(self, p: QPainter, pixmap: QPixmap | None, y_off: int) -> None:
        """Paint one framed strip (or the placeholder), shifted vertically by
        `y_off`. Frame size is driven by the image aspect, centered, then offset."""
        w, h = self.width(), self.height()
        s = min(w, h)
        border = scale_px(8, s, minimum=2)
        radius = scale_px(6, s, minimum=2)

        if pixmap is not None:
            pw, ph = pixmap.width(), pixmap.height()
            aspect = pw / ph if ph else 1.0
        else:
            # Placeholder keeps the booth-strip 2:3 portrait shape.
            aspect = 2 / 3

        # Largest content rect of `aspect` that fits inside the widget,
        # leaving room for the border, centered.
        avail_w = max(1, w - 2 * border)
        avail_h = max(1, h - 2 * border)
        if avail_w / avail_h > aspect:
            img_h = avail_h
            img_w = int(img_h * aspect)
        else:
            img_w = avail_w
            img_h = int(img_w / aspect)
        fx = (w - img_w) // 2
        fy = (h - img_h) // 2 + y_off
        frame = QRectF(fx - border, fy - border,
                       img_w + 2 * border, img_h + 2 * border)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(FRAME_COLOR))
        p.drawRoundedRect(frame, radius, radius)

        if pixmap is not None:
            scaled = pixmap.scaled(
                img_w, img_h,
                Qt.AspectRatioMode.IgnoreAspectRatio,  # already aspect-matched
                Qt.TransformationMode.SmoothTransformation,
            )
            p.drawPixmap(fx, fy, scaled)
        else:
            p.fillRect(fx, fy, img_w, img_h, PANE_BG)
            if self._placeholder:
                font = QFont()
                font.setPointSize(scale_px(28, s, minimum=14))
                font.setWeight(QFont.Weight.Bold)
                p.setFont(font)
                p.setPen(QColor(196, 181, 154))
                p.drawText(QRectF(fx, fy, img_w, img_h),
                           int(Qt.AlignmentFlag.AlignCenter), self._placeholder)


class AttractWidget(QWidget):
    def __init__(self, cfg: Config, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        # See _FramedImage: a plain QWidget needs this for its own
        # background-color to paint instead of the main window's #111.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("background-color: #0e0c0a;")

        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(48, 36, 48, 24)
        self._outer.setSpacing(20)

        # --- Title ---
        self._title = QLabel("PHOTOBOOTH", self)
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._outer.addWidget(self._title)

        # --- Middle: carousel + QR / share URL. Direction flips with
        # orientation (side-by-side on landscape, stacked on portrait). ---
        self._middle = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self._middle.setSpacing(36)
        self._outer.addLayout(self._middle, 1)

        # Carousel pane — frame hugs the strip image, see _FramedImage.
        self._carousel = _FramedImage(self)
        self._carousel.set_placeholder("Take your\nfirst photo!")
        self._middle.addWidget(self._carousel, 3)

        # Share/QR pane
        share_pane = QWidget(self)
        share_layout = QVBoxLayout(share_pane)
        share_layout.setContentsMargins(0, 0, 0, 0)
        share_layout.setSpacing(12)
        share_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._share_caption = QLabel(cfg.display.share_caption, share_pane)
        self._share_caption.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._share_url_label = QLabel(cfg.display.share_url, share_pane)
        self._share_url_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Never wrap the URL — _apply_responsive_styles shrinks the font to
        # fit the share pane width instead (see _fit_url_font_px).
        self._share_url_label.setWordWrap(False)

        self._qr_label = QLabel(share_pane)
        self._qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._qr_label.setStyleSheet(
            "background-color: white; border-radius: 8px; padding: 10px;"
        )

        if cfg.display.share_url:
            share_layout.addWidget(self._share_caption)
            share_layout.addWidget(self._share_url_label)
            # The QR is a fixed-size label; center it under the text explicitly
            # (a fixed-size widget otherwise left-aligns in its layout cell).
            share_layout.addWidget(
                self._qr_label, 0, Qt.AlignmentFlag.AlignHCenter
            )
        else:
            self._share_caption.hide()
            self._share_url_label.hide()
            self._qr_label.hide()

        self._middle.addWidget(share_pane, 2, Qt.AlignmentFlag.AlignCenter)

        # --- CTA ---
        self._cta = QLabel("Press SPACE to start", self)
        self._cta.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._outer.addWidget(self._cta)

        # --- Footer: upload status (always visible) ---
        self._upload_line = QLabel("", self)
        self._upload_line.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._outer.addWidget(self._upload_line)

        self._apply_responsive_styles()

        # --- State ---
        self._strips: list[Path] = []
        self._strip_pixmaps: dict[Path, QPixmap] = {}
        self._carousel_index = 0
        self._carousel_queue: list[int] = []  # shuffled play order
        self._pending_count = 0
        self._status_text = "Starting…"

        self._carousel_timer = QTimer(self)
        self._carousel_timer.timeout.connect(self._tick_carousel)
        self._carousel_timer.start(int(cfg.display.carousel_seconds * 1000))

        self._rescan_timer = QTimer(self)
        self._rescan_timer.timeout.connect(self._rescan_strips)
        self._rescan_timer.start(STRIPS_RESCAN_MS)

        # Build QR + initial scan now.
        self._qr_box_size = 0
        self._refresh_qr()
        self._rescan_strips()
        self._refresh_footer()

    # ------------------------------------------------------------------ enter

    def on_enter(self) -> None:
        # Rescan immediately so a strip just produced shows up.
        self._rescan_strips()

    # ------------------------------------------------------------------ public hooks

    def set_pending_count(self, count: int) -> None:
        self._pending_count = count
        self._refresh_footer()

    def set_upload_status(self, status: str) -> None:
        self._status_text = status
        self._refresh_footer()

    # ------------------------------------------------------------------ carousel

    def _rescan_strips(self) -> None:
        strips_dir = self.cfg.strips_dir
        if not strips_dir.exists():
            new_strips: list[Path] = []
        else:
            new_strips = sorted(
                strips_dir.glob("*.jpg"), key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        if new_strips == self._strips:
            return
        self._strips = new_strips
        # Drop any cached pixmaps for strips that are gone.
        keep = set(new_strips)
        self._strip_pixmaps = {p: q for p, q in self._strip_pixmaps.items() if p in keep}
        # Show the newest strip immediately (index 0 = most recent), and clear
        # the shuffle bag so it's rebuilt over the new set on the next tick.
        self._carousel_index = 0
        self._carousel_queue = []
        self._render_carousel()

    def _tick_carousel(self) -> None:
        # Rotate through strips in a *random* order: draw from a shuffle bag so
        # every strip is shown once before any repeats, then reshuffle.
        n = len(self._strips)
        if n <= 1:
            return
        if not self._carousel_queue:
            self._carousel_queue = list(range(n))
            random.shuffle(self._carousel_queue)
            # Avoid an immediate repeat across the shuffle boundary.
            if self._carousel_queue[0] == self._carousel_index:
                self._carousel_queue.append(self._carousel_queue.pop(0))
        self._carousel_index = self._carousel_queue.pop(0)
        self._render_carousel(animate=True)

    def _render_carousel(self, animate: bool = False) -> None:
        if not self._strips:
            self._carousel.set_pixmap(None)  # shows the placeholder
            return
        path = self._strips[self._carousel_index]
        if path not in self._strip_pixmaps:
            pix = QPixmap(str(path))
            if pix.isNull():
                return
            self._strip_pixmaps[path] = pix
        # _FramedImage handles fit-to-aspect + framing; just hand it the pixmap.
        # Animated swaps (carousel ticks) slide; rescans/resizes snap.
        if animate:
            self._carousel.animate_to(self._strip_pixmaps[path])
        else:
            self._carousel.set_pixmap(self._strip_pixmaps[path])

    # ------------------------------------------------------------------ QR

    def _refresh_qr(self) -> None:
        url = self.cfg.display.share_url
        if not url:
            return
        # Size the QR modules off the screen height so the code stays legible
        # but never dominates the share pane. Regenerated only when the box
        # size actually changes (resize), not on every layout pass.
        box_size = scale_px(8, short_side(self), minimum=3)
        if box_size == self._qr_box_size:
            return
        self._qr_box_size = box_size
        try:
            qr = qrcode.QRCode(
                version=None,
                error_correction=qrcode.constants.ERROR_CORRECT_M,
                box_size=box_size,
                border=2,
            )
            qr.add_data(url)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            pix = QPixmap()
            pix.loadFromData(buf.getvalue(), "PNG")
            self._qr_label.setPixmap(pix)
            self._qr_label.setFixedSize(pix.width() + 20, pix.height() + 20)
        except Exception as e:
            LOG.warning("could not generate QR code: %s", e)

    # ------------------------------------------------------------------ footer

    def _refresh_footer(self) -> None:
        parts = []
        if self._pending_count > 0:
            noun = "photo" if self._pending_count == 1 else "photos"
            parts.append(f"⤴ {self._pending_count} {noun} uploading")
        else:
            parts.append("✓ All photos uploaded")
        if self._status_text and self._status_text not in ("Idle", "Stopped", ""):
            parts.append(self._status_text)
        self._upload_line.setText("    ·    ".join(parts))

    def _url_width_budget(self, w: int, h: int, s: int, landscape: bool) -> int:
        """Width (px) the share-pane URL line may occupy on one line."""
        margin = scale_px(48, s, minimum=8)
        spacing = scale_px(36, s, minimum=8)
        if landscape:
            # middle splits carousel:share = 3:2 along the width.
            middle_w = max(1, w - 2 * margin)
            pane_w = (middle_w - spacing) * 2 // 5
        else:
            pane_w = w - 2 * margin
        return max(60, pane_w - scale_px(16, s, minimum=4))

    def _fit_url_font_px(self, w: int, h: int, s: int, landscape: bool) -> int:
        """Largest URL font (px) that fits the share pane on one line.

        Starts at the design size and shrinks until the text fits the width
        the share pane actually gets from the layout, so a long URL never
        wraps or runs into the strip — it just gets smaller."""
        url = self.cfg.display.share_url or ""
        design = scale_px(22, s, minimum=11)
        if not url:
            return design
        budget = self._url_width_budget(w, h, s, landscape)
        size = design
        font = QFont()
        while size > 9:
            font.setPixelSize(size)
            if QFontMetrics(font).horizontalAdvance(url) <= budget:
                break
            size -= 1
        return size

    def _apply_responsive_styles(self) -> None:
        """Re-derive fonts/margins from the short side and flip the middle
        section between side-by-side (landscape) and stacked (portrait)."""
        w, h = self.width(), self.height()
        s = min(w, h)

        # Orientation: lay the carousel and share pane along the long axis.
        landscape = w >= h
        self._middle.setDirection(
            QBoxLayout.Direction.LeftToRight if landscape
            else QBoxLayout.Direction.TopToBottom
        )

        self._outer.setContentsMargins(
            scale_px(48, s, minimum=8), scale_px(36, s, minimum=6),
            scale_px(48, s, minimum=8), scale_px(24, s, minimum=4),
        )
        self._outer.setSpacing(scale_px(20, s, minimum=4))
        self._middle.setSpacing(scale_px(36, s, minimum=8))

        self._title.setStyleSheet(
            f"color: #f5e8c8; font-size: {scale_px(84, s, minimum=24)}px;"
            f" font-weight: 800; letter-spacing: {scale_px(8, s, minimum=1)}px;"
        )
        self._share_caption.setStyleSheet(
            f"color: #c4b59a; font-size: {scale_px(28, s, minimum=12)}px;"
            " font-weight: 600; letter-spacing: 1px;"
        )
        url_px = self._fit_url_font_px(w, h, s, landscape)
        self._share_url_label.setStyleSheet(
            f"color: #f5e8c8; font-size: {url_px}px; font-weight: 400;"
        )
        self._cta.setStyleSheet(
            f"color: #ccc; font-size: {scale_px(36, s, minimum=14)}px;"
            f" font-weight: 400; letter-spacing: {scale_px(2, s, minimum=1)}px;"
        )
        self._upload_line.setStyleSheet(
            f"color: #888; font-size: {scale_px(16, s, minimum=10)}px;"
        )

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._apply_responsive_styles()
        self._refresh_qr()
        self._render_carousel()
