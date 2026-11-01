"""Comparison sheets: rows of images scaled to a common height, with small labels."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from .errors import UsageError


def build_sheet(
    rows: list[list[tuple[str, Path]]], out: Path, row_height: int = 600, max_width: int = 3200, gap: int = 8
) -> dict:
    """rows: list of rows, each a list of (label, image path). Missing images become grey placeholders."""
    if not rows or not any(rows):
        raise UsageError("nothing to put on the sheet")
    rendered_rows = []
    missing = []
    for row in rows:
        ims = []
        for label, path in row:
            if path is not None and Path(path).is_file():
                im = Image.open(path).convert("RGB")
                im = im.resize((max(1, int(im.width * row_height / im.height)), row_height))
            else:
                missing.append(str(path))
                im = Image.new("RGB", (int(row_height * 0.75), row_height), (128, 128, 128))
            d = ImageDraw.Draw(im)
            d.rectangle((0, 0, 8 + 7 * len(label), 18), fill=(0, 0, 0))
            d.text((4, 3), label, fill=(255, 255, 255))
            ims.append(im)
        rendered_rows.append(ims)
    width = max(sum(i.width for i in r) + gap * (len(r) - 1) for r in rendered_rows)
    height = row_height * len(rendered_rows) + gap * (len(rendered_rows) - 1)
    sheet = Image.new("RGB", (width, height), (24, 24, 24))
    y = 0
    for r in rendered_rows:
        x = 0
        for im in r:
            sheet.paste(im, (x, y))
            x += im.width + gap
        y += row_height + gap
    if sheet.width > max_width:
        sheet = sheet.resize((max_width, int(sheet.height * max_width / sheet.width)))
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, quality=90)
    return {"sheet": str(out), "size": [sheet.width, sheet.height], "missing": missing}
