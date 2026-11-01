"""Camera geometry shared with blender_scripts/common.py, usable without Blender.

Keep these numbers in sync with HEIGHT, CAMERAS["front"] and ORTHO_SCALE in blender_scripts/common.py
(tests/test_orientation_geometry_tools.py checks that they match).
"""

from __future__ import annotations

from .errors import UsageError

HEIGHT = 2.9
FRONT_CAM_Z = 1.45
ORTHO_SCALE = 3.3


def pixel_to_figure(px: float, py: float, res: str = "900x1200") -> dict:
    """Pixel in the orthographic front render -> (X, Z) as fractions of the figure height, for `prep --blush`.

    Blender's orthographic scale spans the larger image dimension. X is positive to the viewer's right (the figure's
    left), Z is the height above the feet.
    """
    try:
        w, h = (int(v) for v in res.lower().split("x"))
    except ValueError as e:
        raise UsageError("--res must look like 900x1200") from e
    if not (0 <= px <= w and 0 <= py <= h):
        raise UsageError(f"pixel ({px}, {py}) is outside a {w}x{h} render")
    units_per_px = ORTHO_SCALE / max(w, h)
    x = (px - w / 2) * units_per_px
    z = FRONT_CAM_Z + (h / 2 - py) * units_per_px
    fx, fz = x / HEIGHT, z / HEIGHT
    return {"x": round(fx, 4), "z": round(fz, 4), "blush_arg": f"--blush={fx:.4f},{fz:.4f}"}
