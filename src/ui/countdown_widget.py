"""Old-timey film-leader countdown.

Sepia palette, grain overlay, big serif numeral, sweeping clock hand,
random scratches and dust. Plays a 24-fps projector rumble underneath
and a sharp wooden tick on each second.

Emits `finished` when the count reaches 0.
"""

from __future__ import annotations

import math
import random

import numpy as np
from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QImage,
    QPainter,
    QPen,
    QRadialGradient,
)
from PyQt6.QtWidgets import QWidget

from ..config import Config
from ..sound import SoundEffect

# Sepia palette
BG_CREAM = QColor(242, 222, 178)
BG_OUTER = QColor(210, 188, 140)
INK_DARK = QColor(50, 35, 20)
INK_LINE = QColor(70, 50, 30)
SCRATCH_LIGHT = QColor(255, 250, 220, 180)
SCRATCH_DARK = QColor(20, 10, 0, 140)

FRAME_FPS = 30


class CountdownWidget(QWidget):
    finished = pyqtSignal()
    pre_fire = pyqtSignal()  # fires `shutter_lead_ms` before `finished`

    def __init__(self, cfg: Config, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.setStyleSheet("background-color: #0a0805;")

        self._tick = SoundEffect(
            cfg.sounds_dir / "film_tick.wav",
            volume=cfg.sound.volume,
            enabled=cfg.sound.enabled,
        )
        self._rumble = SoundEffect(
            cfg.sounds_dir / "film_rumble.wav",
            volume=cfg.sound.volume * 0.7,
            enabled=cfg.sound.enabled,
            loop=True,
        )

        self._remaining = 0
        self._sweep_angle_deg = 0.0
        self._pre_fire_fired = False

        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(1000)
        self._tick_timer.timeout.connect(self._on_second)

        self._pre_fire_timer = QTimer(self)
        self._pre_fire_timer.setSingleShot(True)
        self._pre_fire_timer.timeout.connect(self._on_pre_fire)

        self._frame_timer = QTimer(self)
        self._frame_timer.setInterval(int(1000 / FRAME_FPS))
        self._frame_timer.timeout.connect(self._on_frame)

        self._rng = random.Random()
        self._grain_seed = 0
        self._font = self._pick_serif_font()

    def _pick_serif_font(self) -> QFont:
        for family in ("Bodoni 72", "Didot", "Times New Roman", "Times",
                       "Georgia", "Cochin"):
            f = QFont(family)
            if f.exactMatch():
                return f
        # Fall back to whatever the system picks for a serif.
        f = QFont()
        f.setStyleHint(QFont.StyleHint.Serif)
        return f

    def on_enter(self) -> None:
        self._remaining = self.cfg.ui.countdown_seconds
        self._sweep_angle_deg = 0.0
        self._pre_fire_fired = False
        self._tick.play()
        self._rumble.play()
        self._tick_timer.start()
        self._frame_timer.start()
        # Pre-fire the shutter `shutter_lead_ms` before the countdown finishes
        # so the real R6 shutter click lines up with the SNAP display.
        lead = max(0, int(self.cfg.ui.shutter_lead_ms))
        total_ms = self._remaining * 1000
        delay = max(0, total_ms - lead)
        self._pre_fire_timer.start(delay)
        self.update()

    def _on_second(self) -> None:
        self._remaining -= 1
        if self._remaining <= 0:
            self._tick_timer.stop()
            self._frame_timer.stop()
            self._rumble.stop()
            # Backstop in case pre_fire didn't fire (e.g. shutter_lead_ms=0).
            if not self._pre_fire_fired:
                self._on_pre_fire()
            self.finished.emit()
            return
        self._tick.play()

    def _on_pre_fire(self) -> None:
        if self._pre_fire_fired:
            return
        self._pre_fire_fired = True
        self.pre_fire.emit()

    def on_exit(self) -> None:
        """Stop all timers + audio when leaving the countdown state. Without
        this, a pre_fire_timer that was started for this session keeps firing
        after we've left (e.g. inactivity timeout, app shutdown), spuriously
        triggering a capture against whatever the camera is now pointing at."""
        self._tick_timer.stop()
        self._pre_fire_timer.stop()
        self._frame_timer.stop()
        try:
            self._rumble.stop()
        except Exception:
            pass

    def _on_frame(self) -> None:
        # One full sweep per second.
        self._sweep_angle_deg = (self._sweep_angle_deg + 360.0 / FRAME_FPS) % 360.0
        self._grain_seed += 1
        self.update()

    # ------------------------------------------------------------------ paint

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        p = QPainter(self)
        p.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
        )

        w, h = self.width(), self.height()
        # Letterbox to a square "film frame" centered on the widget. The
        # surround margin scales with the display so a small screen isn't
        # eaten by a fixed 80px border (and a huge one isn't cramped).
        margin = max(8, int(min(w, h) * 0.037))  # ~40px each side at 1080p
        frame_size = min(w, h) - 2 * margin
        fx = (w - frame_size) // 2
        fy = (h - frame_size) // 2
        frame_rect = QRectF(fx, fy, frame_size, frame_size)

        # 1) Background sepia with vignette.
        grad = QRadialGradient(
            QPointF(fx + frame_size / 2, fy + frame_size / 2),
            frame_size * 0.7,
        )
        grad.setColorAt(0.0, BG_CREAM)
        grad.setColorAt(1.0, BG_OUTER)
        p.fillRect(frame_rect, QBrush(grad))

        # 2) Concentric guide circles (SMPTE leader style).
        p.setPen(QPen(INK_LINE, max(2, frame_size // 400)))
        for r_frac in (0.40, 0.28, 0.18):
            r = frame_size * r_frac
            p.drawEllipse(
                QPointF(fx + frame_size / 2, fy + frame_size / 2), r, r,
            )

        # 3) Crosshair through center.
        cx = fx + frame_size / 2
        cy = fy + frame_size / 2
        p.setPen(QPen(INK_LINE, max(1, frame_size // 600)))
        p.drawLine(QPointF(fx + frame_size * 0.08, cy),
                   QPointF(fx + frame_size * 0.92, cy))
        p.drawLine(QPointF(cx, fy + frame_size * 0.08),
                   QPointF(cx, fy + frame_size * 0.92))

        # 4) Sweeping clock hand (one rotation per second).
        sweep_r = frame_size * 0.40
        angle_rad = math.radians(self._sweep_angle_deg - 90)  # 0° at top
        sx = cx + math.cos(angle_rad) * sweep_r
        sy = cy + math.sin(angle_rad) * sweep_r
        p.setPen(QPen(INK_DARK, max(3, frame_size // 200)))
        p.drawLine(QPointF(cx, cy), QPointF(sx, sy))

        # 5) Big numeral.
        self._font.setPointSize(max(24, frame_size // 4))
        self._font.setWeight(QFont.Weight.Bold)
        p.setFont(self._font)
        p.setPen(QPen(INK_DARK))
        text = str(self._remaining) if self._remaining > 0 else ""
        if text:
            p.drawText(frame_rect, Qt.AlignmentFlag.AlignCenter, text)

        # 6) Random scratches.
        scratches = self._rng_for_frame()
        for _ in range(scratches.randint(0, 3)):
            x = scratches.randint(fx, fx + frame_size)
            top = scratches.randint(fy, fy + frame_size // 3)
            bot = scratches.randint(fy + frame_size * 2 // 3, fy + frame_size)
            color = SCRATCH_LIGHT if scratches.random() < 0.6 else SCRATCH_DARK
            p.setPen(QPen(color, scratches.randint(1, 3)))
            p.drawLine(x, top, x, bot)

        # 7) Dust specks.
        for _ in range(scratches.randint(4, 10)):
            x = scratches.randint(fx, fx + frame_size)
            y = scratches.randint(fy, fy + frame_size)
            r = scratches.randint(1, 4)
            color = SCRATCH_DARK if scratches.random() < 0.5 else SCRATCH_LIGHT
            p.setBrush(QBrush(color))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(x, y), r, r)

        # 8) Grain overlay on top.
        grain = self._make_grain_image(frame_size // 4, frame_size // 4)
        p.setOpacity(0.18)
        p.drawImage(frame_rect, grain)
        p.setOpacity(1.0)

        # 9) Black mask outside the frame.
        p.setBrush(QBrush(QColor(8, 6, 4)))
        p.setPen(Qt.PenStyle.NoPen)
        if fx > 0:
            p.drawRect(0, 0, fx, h)
            p.drawRect(fx + frame_size, 0, w - (fx + frame_size), h)
        if fy > 0:
            p.drawRect(0, 0, w, fy)
            p.drawRect(0, fy + frame_size, w, h - (fy + frame_size))

        p.end()

    def _rng_for_frame(self) -> random.Random:
        # Deterministic per-frame so the scratches "exist" for one frame
        # then change — that's the film-grain feel.
        return random.Random(self._grain_seed)

    def _make_grain_image(self, w: int, h: int) -> QImage:
        # Generate luminance noise with numpy then convert to QImage.
        rng = np.random.default_rng(self._grain_seed)
        noise = rng.integers(0, 60, size=(h, w), dtype=np.uint8)
        # Pack into ARGB32 — alpha will be applied via painter opacity.
        argb = np.zeros((h, w, 4), dtype=np.uint8)
        argb[..., 0] = noise  # B
        argb[..., 1] = noise  # G
        argb[..., 2] = noise  # R
        argb[..., 3] = 255    # A
        img = QImage(argb.tobytes(), w, h, w * 4, QImage.Format.Format_ARGB32)
        return img.copy()  # detach from numpy buffer
