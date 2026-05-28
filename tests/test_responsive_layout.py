"""Layout is correct in BOTH portrait and landscape orientations.

The UI was originally authored for a portrait monitor and baked that
orientation in. These tests pin the orientation-agnostic behaviour:

  * type scales off the short side (orientation-symmetric),
  * the attract screen reflows side-by-side <-> stacked,
  * photos lay out as a row (landscape) or column (portrait),
  * the picker uses more columns when wide than when tall,
  * a long share URL shrinks to one line instead of wrapping,
  * solid backgrounds actually paint (white SNAP flash, dark attract).

Run with: QT_QPA_PLATFORM=offscreen pytest tests/test_responsive_layout.py
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QFont, QFontMetrics
from PyQt6.QtWidgets import QApplication, QBoxLayout

from src.config import Config, ImmichConfig
from src.ui.attract_widget import AttractWidget
from src.ui.background_picker import BackgroundPicker
from src.ui.booth_window import BoothState, BoothWindow
from src.ui.review_widget import ReviewWidget
from src.ui.scale import scale_px, short_side

LANDSCAPE = (1728, 1117)
PORTRAIT = (1117, 1728)


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def cfg(tmp_path: Path):
    c = Config()
    c.immich = ImmichConfig(base_url="http://x", api_key="x")
    c.display.share_url = "https://immich.example.com/s/testing"
    c.display.share_caption = "See your photos at"
    return c


def _color(img, x, y):
    c = img.pixelColor(x, y)
    return (c.red(), c.green(), c.blue())


# --------------------------------------------------------------- scale helper

def test_scale_px_is_orientation_symmetric():
    # Same short side -> same size, whichever axis is which.
    assert scale_px(84, min(*LANDSCAPE)) == scale_px(84, min(*PORTRAIT))


def test_scale_px_tracks_short_side():
    assert scale_px(100, 1080) == 100          # baseline
    assert scale_px(100, 540) == 50            # half short side -> half size
    assert scale_px(100, 2160) == 200          # double
    assert scale_px(4, 100, minimum=2) == 2    # floor respected


# --------------------------------------------------------------- attract reflow

@pytest.mark.parametrize("size,expected", [
    (LANDSCAPE, QBoxLayout.Direction.LeftToRight),
    (PORTRAIT, QBoxLayout.Direction.TopToBottom),
])
def test_attract_middle_reflows_with_orientation(app, cfg, size, expected):
    a = AttractWidget(cfg)
    a.resize(*size)
    a._apply_responsive_styles()
    assert a._middle.direction() == expected


@pytest.mark.parametrize("size", [LANDSCAPE, PORTRAIT])
def test_attract_url_never_wraps_and_fits(app, cfg, size):
    cfg.display.share_url = (
        "https://immich.silentmedia.example.com/share/2026-spring-festival/xyz"
    )
    a = AttractWidget(cfg)
    a.resize(*size)
    a._apply_responsive_styles()
    assert a._share_url_label.wordWrap() is False

    w, h = size
    s = short_side(a)
    landscape = w >= h
    design = scale_px(22, s)
    px = a._fit_url_font_px(w, h, s, landscape)
    budget = a._url_width_budget(w, h, s, landscape)

    # Never below the floor, never above the design size.
    assert 9 <= px <= design
    # At the chosen size it fits the pane on one line.
    font = QFont(); font.setPixelSize(px)
    assert QFontMetrics(font).horizontalAdvance(cfg.display.share_url) <= budget
    # It's the largest that fits: if it shrank at all, +1px would overflow.
    if px < design:
        font.setPixelSize(px + 1)
        assert QFontMetrics(font).horizontalAdvance(cfg.display.share_url) > budget
    # The narrow landscape share pane forces this long URL to shrink.
    if landscape:
        assert px < design


# --------------------------------------------------------------- attract carousel

def test_qr_centered_under_text(app, cfg):
    win = BoothWindow(cfg, enable_camera=False)
    win.show()
    app.processEvents()
    a = win._widgets[BoothState.ATTRACT]
    # QR and caption are siblings in the share pane; centered share a center x.
    qr_cx = a._qr_label.x() + a._qr_label.width() / 2
    cap_cx = a._share_caption.x() + a._share_caption.width() / 2
    assert abs(qr_cx - cap_cx) <= 2
    win.close()


def test_carousel_order_is_shuffled_bag(app, cfg):
    a = AttractWidget(cfg)
    n = 6
    a._strips = [Path(f"strip_{i}.jpg") for i in range(n)]
    a._carousel_index = 0
    a._carousel_queue = []

    seen = []
    prev = a._carousel_index
    for _ in range(n):                 # one full cycle
        a._tick_carousel()
        seen.append(a._carousel_index)
        assert a._carousel_index != prev   # never an immediate repeat
        prev = a._carousel_index

    # A shuffle bag shows every strip exactly once per cycle (not sequential).
    assert sorted(seen) == list(range(n))


# --------------------------------------------------------------- picker columns

def test_picker_more_columns_when_wide(app, cfg, tmp_path):
    bg = tmp_path / "backgrounds"
    bg.mkdir()
    from PIL import Image
    for i in range(8):
        Image.new("RGB", (200, 133), (i * 20, i * 20, i * 20)).save(bg / f"b{i}.jpg")
    type(cfg).backgrounds_dir = property(lambda self, _d=bg: _d)

    p = BackgroundPicker(cfg)
    p.resize(*LANDSCAPE)
    land_cols = p._grid_cols()
    p.resize(*PORTRAIT)
    port_cols = p._grid_cols()

    assert land_cols >= 1 and port_cols >= 1
    assert land_cols > port_cols  # wide screen packs more across


# --------------------------------------------------------------- review row/col

@pytest.mark.parametrize("size,horizontal", [(LANDSCAPE, True), (PORTRAIT, False)])
def test_review_orientation(app, cfg, size, horizontal):
    r = ReviewWidget(cfg)
    r.resize(*size)
    lay = r.photo_layout()
    cells = lay["cells"]
    assert lay["horizontal"] is horizontal
    assert len(cells) == 3

    xs = [c[0] for c in cells]
    ys = [c[1] for c in cells]
    if horizontal:
        assert len(set(ys)) == 1          # a row: shared baseline
        assert xs == sorted(xs)           # left-to-right
    else:
        assert len(set(xs)) == 1          # a column: shared left edge
        assert ys == sorted(ys)           # top-to-bottom

    # Photos keep their 3:2 aspect and stay on screen.
    assert abs(lay["photo_w"] / lay["photo_h"] - 3 / 2) < 0.05
    w, h = size
    for (cx, cy) in cells:
        assert cx >= 0 and cy >= 0
        assert cx + lay["cell_w"] <= w
        assert cy + lay["cell_h"] <= h


# --------------------------------------------------------------- backgrounds paint

def test_capture_flash_is_white_not_inherited_grey(app, cfg):
    """Regression: a plain QWidget ignores its own background-color unless
    WA_StyledBackground is set, so the SNAP flash showed the window's #111."""
    win = BoothWindow(cfg, enable_camera=False)
    win.show()
    app.processEvents()
    win.transition_to(BoothState.CAPTURE)
    app.processEvents()
    img = win.grab().toImage()
    assert _color(img, 4, 4) == (255, 255, 255)
    win.close()


def test_attract_background_is_uniform(app, cfg):
    """No black custom-paint boxes sitting on a lighter screen: the corner
    and the carousel interior should be the same warm-dark colour."""
    win = BoothWindow(cfg, enable_camera=False)
    win.show()
    app.processEvents()
    win.transition_to(BoothState.ATTRACT)
    app.processEvents()
    attract = win._widgets[BoothState.ATTRACT]
    img = win.grab().toImage()
    geom = attract._carousel.geometry()
    corner = _color(img, 4, 4)
    inside = _color(img, geom.x() + 6, geom.y() + geom.height() // 2)
    assert corner == (14, 12, 10)     # #0e0c0a actually painted
    assert inside == corner           # framed-image fill matches the screen
    win.close()
