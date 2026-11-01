"""Split a turnaround sheet into separate views by runs of background-coloured columns.

The sheet has a flat background and the figures stand in one row. A column belongs to a figure when enough of its
pixels differ from the background colour; consecutive figure columns form a run. Every view is cut to the same width
and the same vertical extent, so the views keep a shared scale and ground line, which the multiview 3D models expect.
Pixels outside a view's own run (a neighbour's hair tip, say) are painted with the background colour.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from .errors import CheckFailed, UsageError
from .workdir import VIEW_NAMES


def load_rgb(path: Path) -> np.ndarray:
    with Image.open(path) as im:
        return np.asarray(im.convert("RGB"), dtype=np.int16)


def background_color(img: np.ndarray, border: int = 8) -> np.ndarray:
    """Median colour of the outer border."""
    h, w, _ = img.shape
    b = max(1, min(border, h // 4, w // 4))
    strips = [img[:b].reshape(-1, 3), img[-b:].reshape(-1, 3), img[:, :b].reshape(-1, 3), img[:, -b:].reshape(-1, 3)]
    return np.median(np.concatenate(strips), axis=0).astype(np.int16)


def foreground_mask(img: np.ndarray, bg: np.ndarray, tolerance: int) -> np.ndarray:
    return np.abs(img - bg[None, None, :]).max(axis=2) > tolerance


def column_runs(
    mask: np.ndarray, min_pixels: int, merge_gap: int, min_width: int
) -> list[tuple[int, int]]:
    """Half-open [start, end) column ranges that contain foreground, after merging small gaps and dropping slivers."""
    cols = mask.sum(axis=0) >= min_pixels
    runs: list[list[int]] = []
    start = None
    for x, on in enumerate(cols):
        if on and start is None:
            start = x
        elif not on and start is not None:
            runs.append([start, x])
            start = None
    if start is not None:
        runs.append([start, len(cols)])
    merged: list[list[int]] = []
    for r in runs:
        if merged and r[0] - merged[-1][1] <= merge_gap:
            merged[-1][1] = r[1]
        else:
            merged.append(r)
    return [(a, b) for a, b in merged if b - a >= min_width]


def row_extent(mask: np.ndarray, min_pixels: int) -> tuple[int, int]:
    rows = np.where(mask.sum(axis=1) >= min_pixels)[0]
    if rows.size == 0:
        return 0, mask.shape[0]
    return int(rows[0]), int(rows[-1]) + 1


def crop_sheet(
    sheet: Path,
    out_dir: Path,
    expected: int = 4,
    names: tuple[str, ...] = VIEW_NAMES,
    tolerance: int = 24,
    pad_frac: float = 0.06,
    merge_gap_frac: float = 0.006,
    min_width_frac: float = 0.03,
) -> dict[str, Any]:
    if not sheet.is_file():
        raise UsageError(f"sheet not found: {sheet}", {"path": str(sheet)})
    if len(names) != expected:
        raise UsageError("number of view names must equal --expected", {"names": names, "expected": expected})
    img = load_rgb(sheet)
    h, w, _ = img.shape
    bg = background_color(img)
    mask = foreground_mask(img, bg, tolerance)
    min_px = max(2, int(h * 0.004))
    runs = column_runs(mask, min_px, merge_gap=int(w * merge_gap_frac), min_width=int(w * min_width_frac))
    info: dict[str, Any] = {
        "sheet": str(sheet),
        "sheet_size": [w, h],
        "background_rgb": [int(c) for c in bg],
        "runs": [list(r) for r in runs],
    }
    if len(runs) != expected:
        hint = (
            "fewer runs than views: two figures touch or overlap (regenerate with more space between views, or lower "
            "--tolerance only if the background is noisy)"
            if len(runs) < expected
            else "more runs than views: a figure is split by a gap or stray marks are present (raise --merge-gap, "
            "or check the sheet for labels and shadows)"
        )
        raise CheckFailed(f"found {len(runs)} figure column runs, expected {expected}", {**info, "hint": hint})

    top, bottom = row_extent(mask, max(2, int(w * 0.002)))
    pad = int(pad_frac * max(b - a for a, b in runs))
    top, bottom = max(0, top - pad), min(h, bottom + pad)
    width = max(b - a for a, b in runs) + 2 * pad
    out_dir.mkdir(parents=True, exist_ok=True)
    src = Image.open(sheet).convert("RGB")
    bg_t = tuple(int(c) for c in bg)
    views: dict[str, Any] = {}
    for i, ((a, b), name) in enumerate(zip(runs, names, strict=True)):
        # columns this view may take: its run plus padding, but never past the midpoint of the gap to a neighbour
        lo = max(a - pad, (runs[i - 1][1] + a) // 2 if i > 0 else 0)
        hi = min(b + pad, (b + runs[i + 1][0]) // 2 if i + 1 < len(runs) else w)
        canvas = Image.new("RGB", (width, bottom - top), bg_t)
        centre = (a + b) // 2
        x0 = centre - width // 2  # sheet x that maps to canvas x = 0
        piece = src.crop((lo, top, hi, bottom))
        canvas.paste(piece, (lo - x0, 0))
        path = out_dir / f"view_{name}.png"
        canvas.save(path)
        views[name] = {"path": str(path), "run": [a, b], "size": [width, bottom - top]}
    info.update({"views": views, "crop_box_rows": [top, bottom], "view_width": width})
    if width < 900:
        info["warning"] = (
            f"each view is only {width} px wide; small features (faces, fingers) will blur in the 3D model. "
            "Generate the sheet at 3840x2160."
        )
    return info
