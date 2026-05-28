"""Adult-only settings overlay (Cmd+,).

Live chroma-key tuning for on-site adjustments. Sliders modify the shared
ChromaKeyer in place, so the live preview reflects changes immediately at
whatever frame rate the camera is delivering.

Persistence model: changes are session-local by default. Tap "Save" to
write them to `runtime_overrides.yaml` (loaded on top of config.yaml on
next launch). Tap "Reset" to clear overrides and revert to config.yaml.
"""

from __future__ import annotations

import logging
from dataclasses import asdict

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..chroma import ChromaKeyer
from ..config import Config, clear_runtime_overrides, write_runtime_overrides

LOG = logging.getLogger(__name__)


# (attr name on ChromaKeyer, label, min, max)
SLIDERS = [
    ("hue_low",            "Green hue range — low",   0, 179),
    ("hue_high",           "Green hue range — high",  0, 179),
    ("sat_min",            "Saturation threshold",    0, 255),
    ("val_min",            "Value (brightness) min",  0, 255),
    ("feather_px_preview", "Feather (live preview)",  0, 30),
    ("feather_px_final",   "Feather (final capture)", 0, 50),
]

TOGGLES = [
    ("spill_suppress", "Spill suppression (kill green halo on edges)"),
    ("guided_filter",  "Guided filter on final capture (cleaner hair)"),
]


class SettingsOverlay(QWidget):
    def __init__(
        self,
        cfg: Config,
        keyer: ChromaKeyer,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.keyer = keyer

        # Snapshot for the "Discard" path.
        self._snapshot = asdict(keyer)

        self.setAutoFillBackground(True)
        self.setStyleSheet(
            "SettingsOverlay { background-color: rgba(0, 0, 0, 230); }"
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addStretch(1)

        card = QWidget(self)
        card.setObjectName("settingsCard")
        card.setStyleSheet(
            "#settingsCard { background-color: #1a1a1a; border: 2px solid #444;"
            " border-radius: 12px; }"
            " QLabel { color: #eee; }"
            " QSlider::groove:horizontal { background: #333; height: 6px;"
            " border-radius: 3px; }"
            " QSlider::handle:horizontal { background: #4af; width: 18px;"
            " height: 18px; margin: -8px 0; border-radius: 9px; }"
            " QCheckBox { color: #eee; spacing: 10px; }"
            " QPushButton { color: #eee; background-color: #333; border: 1px solid #555;"
            " padding: 8px 18px; border-radius: 6px; font-size: 16px; }"
            " QPushButton:hover { background-color: #444; }"
            " QPushButton#save { background-color: #2a662a; }"
            " QPushButton#reset { background-color: #663a2a; }"
        )
        card.setFixedWidth(900)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(40, 32, 40, 32)
        card_layout.setSpacing(14)

        title = QLabel("Chroma Key Settings", card)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("color: #f5e8c8; font-size: 32px; font-weight: 700;")
        card_layout.addWidget(title)

        hint = QLabel("Sliders update the live preview immediately. Cmd+, to close.", card)
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet("color: #888; font-size: 14px; padding-bottom: 8px;")
        card_layout.addWidget(hint)

        self._value_labels: dict[str, QLabel] = {}
        self._sliders: dict[str, QSlider] = {}
        for attr, label_text, lo, hi in SLIDERS:
            row = self._build_slider_row(card, attr, label_text, lo, hi)
            card_layout.addLayout(row)

        self._checkboxes: dict[str, QCheckBox] = {}
        for attr, label_text in TOGGLES:
            cb = QCheckBox(label_text, card)
            cb.setChecked(bool(getattr(self.keyer, attr)))
            cb.stateChanged.connect(
                lambda state, a=attr: self._on_toggle(a, state)
            )
            self._checkboxes[attr] = cb
            card_layout.addWidget(cb)

        button_row = QHBoxLayout()
        button_row.setSpacing(12)
        button_row.addStretch(1)

        reset_btn = QPushButton("Reset to config", card)
        reset_btn.setObjectName("reset")
        reset_btn.clicked.connect(self._on_reset)
        button_row.addWidget(reset_btn)

        discard_btn = QPushButton("Discard changes", card)
        discard_btn.clicked.connect(self._on_discard)
        button_row.addWidget(discard_btn)

        save_btn = QPushButton("Save to config", card)
        save_btn.setObjectName("save")
        save_btn.clicked.connect(self._on_save)
        button_row.addWidget(save_btn)

        close_btn = QPushButton("Close", card)
        close_btn.clicked.connect(self.hide)
        button_row.addWidget(close_btn)

        button_row.addStretch(1)
        card_layout.addSpacing(8)
        card_layout.addLayout(button_row)

        self._status_label = QLabel("", card)
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_label.setStyleSheet("color: #4af; font-size: 13px;")
        card_layout.addWidget(self._status_label)

        # Center the card horizontally.
        center_row = QHBoxLayout()
        center_row.addStretch(1)
        center_row.addWidget(card)
        center_row.addStretch(1)
        outer.addLayout(center_row)
        outer.addStretch(1)

    def _build_slider_row(
        self, parent: QWidget, attr: str, label_text: str, lo: int, hi: int,
    ) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(12)

        label = QLabel(label_text, parent)
        label.setMinimumWidth(280)
        label.setStyleSheet("font-size: 15px;")
        row.addWidget(label)

        slider = QSlider(Qt.Orientation.Horizontal, parent)
        slider.setRange(lo, hi)
        slider.setValue(int(getattr(self.keyer, attr)))
        slider.valueChanged.connect(lambda v, a=attr: self._on_slider(a, v))
        row.addWidget(slider, 1)

        value_label = QLabel(str(int(getattr(self.keyer, attr))), parent)
        value_label.setFixedWidth(48)
        value_label.setStyleSheet("font-size: 15px; font-weight: 700; color: #4af;")
        value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(value_label)

        self._sliders[attr] = slider
        self._value_labels[attr] = value_label
        return row

    # ------------------------------------------------------------------ events

    def _on_slider(self, attr: str, value: int) -> None:
        setattr(self.keyer, attr, value)
        self._value_labels[attr].setText(str(value))
        self._status_label.setText("Session-only — tap Save to persist")

    def _on_toggle(self, attr: str, state: int) -> None:
        setattr(self.keyer, attr, bool(state))
        self._status_label.setText("Session-only — tap Save to persist")

    def _on_save(self) -> None:
        updates = {"chroma": {attr: getattr(self.keyer, attr)
                              for attr, *_ in SLIDERS}}
        updates["chroma"].update({attr: getattr(self.keyer, attr)
                                  for attr, _ in TOGGLES})
        try:
            path = write_runtime_overrides(updates)
            LOG.info("settings: saved overrides to %s", path)
            self._status_label.setText(f"Saved → {path.name}")
            # Update snapshot so subsequent "Discard" reverts to this point.
            self._snapshot = asdict(self.keyer)
        except Exception as e:
            LOG.exception("save failed")
            self._status_label.setText(f"Save failed: {e.__class__.__name__}")

    def _on_reset(self) -> None:
        # Reload original values from config.yaml (which lives in self.cfg —
        # it was loaded BEFORE any overrides we may have written).
        try:
            clear_runtime_overrides()
        except Exception as e:
            LOG.warning("could not clear overrides: %s", e)
        # Re-apply config defaults to the live keyer.
        c = self.cfg.chroma
        for attr, *_ in SLIDERS:
            setattr(self.keyer, attr, int(getattr(c, attr)))
        for attr, _ in TOGGLES:
            setattr(self.keyer, attr, bool(getattr(c, attr)))
        self._reflect_into_ui()
        self._snapshot = asdict(self.keyer)
        self._status_label.setText("Reset to config.yaml values")

    def _on_discard(self) -> None:
        # Restore the snapshot taken when the overlay last opened (or was saved).
        for k, v in self._snapshot.items():
            setattr(self.keyer, k, v)
        self._reflect_into_ui()
        self._status_label.setText("Discarded changes")

    def _reflect_into_ui(self) -> None:
        """Push current keyer values back into the sliders/checkboxes."""
        for attr, slider in self._sliders.items():
            slider.blockSignals(True)
            slider.setValue(int(getattr(self.keyer, attr)))
            slider.blockSignals(False)
            self._value_labels[attr].setText(str(int(getattr(self.keyer, attr))))
        for attr, cb in self._checkboxes.items():
            cb.blockSignals(True)
            cb.setChecked(bool(getattr(self.keyer, attr)))
            cb.blockSignals(False)

    def open(self) -> None:
        """Show the overlay and take a fresh snapshot for Discard."""
        self._snapshot = asdict(self.keyer)
        self._reflect_into_ui()
        self._status_label.setText("")
        self.show()
        self.raise_()
