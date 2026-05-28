"""Orientation-agnostic UI scaling.

The booth's type sizes were originally hardcoded against a single *portrait*
monitor. The mistake baked in there wasn't the pixel values — it was deriving
sizes from one fixed axis. On a landscape screen that axis flips meaning and
the layout falls apart.

`scale_px` instead scales against the **short side** of the surface
(`min(width, height)`). The short side is the dimension that actually
constrains how big text and frames can be, and it's stable regardless of
orientation: a 1080×1920 portrait screen and a 1920×1080 landscape screen
both get the same type size. The baseline is a 1080px short side, so the
original look is preserved on a 1080-wide portrait display and scales
proportionally everywhere else.

Sizing of images/frames is *not* this helper's job — those should be fit to
their own content aspect and centered (see the framed-image carousel and the
review/countdown painters), never stretched to fill the screen.
"""

from __future__ import annotations

DESIGN_SHORT_SIDE = 1080


def scale_px(value: float, short_side: int, *, minimum: int = 1) -> int:
    """Scale a design-pixel `value` for a surface whose short side (the
    smaller of width/height) is `short_side`.

    Returns `value` (rounded) at the design short side, scaling linearly
    otherwise. `minimum` keeps borders/letter-spacing from collapsing to 0.
    A non-positive `short_side` (widget not laid out yet) returns the
    unscaled value.
    """
    if short_side <= 0:
        return max(minimum, round(value))
    return max(minimum, round(value * short_side / DESIGN_SHORT_SIDE))


def short_side(widget) -> int:
    """Convenience: the constraining dimension of a widget."""
    return min(widget.width(), widget.height())
