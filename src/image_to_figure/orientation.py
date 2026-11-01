"""Pick the rotation about Z that makes a model face the camera, by comparing flat renders with the front view.

Image-to-3D providers do not agree on which way their GLBs face after import (Tripo's face +X, for example). The model
is rendered flat-shaded from the front at 0, 90, 180 and 270 degrees, each render is compared with the turnaround's
front view (silhouette overlap plus colour difference), and the best rotation wins. Front and back silhouettes are
nearly mirror images, so the colour term does most of the work there: a face and a back of the head differ a lot.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from .crop import background_color, foreground_mask

GRID = (64, 128)  # width, height after normalising the subject's bounding box


def _normalise(rgb: np.ndarray, mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Crop to the mask's bounding box, letterbox to GRID's aspect, resize. Returns (rgb float 0-1, mask bool)."""
    ys, xs = np.where(mask)
    if ys.size == 0:
        return np.zeros((GRID[1], GRID[0], 3)), np.zeros((GRID[1], GRID[0]), bool)
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    rgb = rgb[y0:y1, x0:x1]
    mask = mask[y0:y1, x0:x1]
    h, w = mask.shape
    target_ratio = GRID[0] / GRID[1]
    if w / h < target_ratio:  # pad width
        new_w = int(round(h * target_ratio))
        pad = (new_w - w) // 2
        rgb = np.pad(rgb, ((0, 0), (pad, new_w - w - pad), (0, 0)))
        mask = np.pad(mask, ((0, 0), (pad, new_w - w - pad)))
    else:
        new_h = int(round(w / target_ratio))
        pad = new_h - h  # pad at the top, keep feet on the bottom edge
        rgb = np.pad(rgb, ((pad, 0), (0, 0), (0, 0)))
        mask = np.pad(mask, ((pad, 0), (0, 0)))
    im = Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8)).resize(GRID, Image.BILINEAR)
    mk = Image.fromarray((mask * 255).astype(np.uint8)).resize(GRID, Image.BILINEAR)
    return np.asarray(im, dtype=np.float64) / 255.0, np.asarray(mk) > 127


def reference_from_view(path: Path, tolerance: int = 24) -> tuple[np.ndarray, np.ndarray]:
    with Image.open(path) as im:
        rgb = np.asarray(im.convert("RGB"), dtype=np.int16)
    mask = foreground_mask(rgb, background_color(rgb), tolerance)
    return _normalise(rgb.astype(np.float64), mask)


def candidate_from_render(path: Path) -> tuple[np.ndarray, np.ndarray]:
    with Image.open(path) as im:
        rgba = np.asarray(im.convert("RGBA"), dtype=np.float64)
    return _normalise(rgba[..., :3], rgba[..., 3] > 127)


def score(ref: tuple[np.ndarray, np.ndarray], cand: tuple[np.ndarray, np.ndarray]) -> dict[str, float]:
    """Lower is better. 0.5 * (1 - IoU) + mean absolute colour difference over the overlap."""
    (rr, rm), (cr, cm) = ref, cand
    union = rm | cm
    inter = rm & cm
    iou = inter.sum() / union.sum() if union.sum() else 0.0
    col = float(np.abs(rr[inter] - cr[inter]).mean()) if inter.sum() else 1.0
    return {"iou": round(float(iou), 4), "colour_diff": round(col, 4), "score": round(0.5 * (1 - iou) + col, 4)}


def choose(ref_view: Path, renders: dict[str, str], hint: float | None = None, margin: float = 0.08) -> dict[str, Any]:
    """renders: {"0": path, "90": path, ...} rendered with the model rotated by that many degrees."""
    ref = reference_from_view(ref_view)
    scores = {k: score(ref, candidate_from_render(Path(p))) for k, p in renders.items()}
    ranked = sorted(scores, key=lambda k: scores[k]["score"])
    best, second = ranked[0], ranked[1] if len(ranked) > 1 else None
    rel = None
    if second is not None:
        b, s = scores[best]["score"], scores[second]["score"]
        rel = round((s - b) / s, 4) if s else 0.0
    out: dict[str, Any] = {"rotation_deg": float(best), "scores": scores, "relative_margin": rel}
    if rel is not None and rel < margin:
        out["ambiguous"] = True
        if hint is not None:
            out["rotation_deg"] = float(hint) % 360
            out["note"] = f"scores too close; used the provider's known orientation ({hint:g} deg)"
        else:
            out["note"] = "scores too close; check the front render and pass --rot explicitly if it is wrong"
    return out
