"""Work directory layout and the spend ledger.

One work directory holds one figure project:

    <workdir>/
      lookdev/        look-dev renders (lookdev_v1.png, ...) and their prompts
      turnaround/     four-view sheets (sheet_v1.png, edits as sheet_v1_e1.png, ...)
      views/          view_front.png, view_left.png, view_back.png, view_right.png (+ crop.json, uploads.json)
      models/<prov>/  provider result.json and downloaded files, model.glb
      figure/         figure.blend (graded master), figure_clean.glb (oriented, decimated, ungraded)
      renders/        review renders and comparison sheets
      turntable/      turntable.mp4 (frames are deleted after encoding unless --keep-frames)
      logs/           Blender and ffmpeg logs
      spend.json      ledger of every paid call made from this directory
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

from .errors import BudgetError

VIEW_NAMES = ("front", "left", "back", "right")


class WorkDir:
    def __init__(self, root: str | os.PathLike):
        self.root = Path(root).expanduser().resolve()

    def sub(self, name: str, create: bool = True) -> Path:
        p = self.root / name
        if create:
            p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def lookdev(self) -> Path:
        return self.sub("lookdev")

    @property
    def turnaround(self) -> Path:
        return self.sub("turnaround")

    @property
    def views(self) -> Path:
        return self.sub("views")

    @property
    def models(self) -> Path:
        return self.sub("models")

    @property
    def figure(self) -> Path:
        return self.sub("figure")

    @property
    def renders(self) -> Path:
        return self.sub("renders")

    @property
    def turntable(self) -> Path:
        return self.sub("turntable")

    @property
    def logs(self) -> Path:
        return self.sub("logs")

    @property
    def ledger_path(self) -> Path:
        return self.root / "spend.json"

    def view_paths(self, views_dir: Path | None = None) -> dict[str, Path]:
        d = views_dir or self.root / "views"
        return {v: d / f"view_{v}.png" for v in VIEW_NAMES}


def next_version(directory: Path, stem: str, suffix: str = ".png") -> Path:
    """directory/stem_v{N}{suffix} with N one higher than any existing version."""
    pat = re.compile(rf"^{re.escape(stem)}_v(\d+){re.escape(suffix)}$")
    n = 0
    if directory.exists():
        for p in directory.iterdir():
            m = pat.match(p.name)
            if m:
                n = max(n, int(m.group(1)))
    return directory / f"{stem}_v{n + 1}{suffix}"


def next_edit(path: Path) -> Path:
    """sheet_v1.png -> sheet_v1_e1.png; sheet_v1_e1.png -> sheet_v1_e2.png (next free number)."""
    m = re.match(r"^(.*?)(?:_e(\d+))?$", path.stem)
    base = m.group(1) if m else path.stem
    n = 1
    while (path.parent / f"{base}_e{n}{path.suffix}").exists():
        n += 1
    return path.parent / f"{base}_e{n}{path.suffix}"


def write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)
    os.replace(tmp, path)


class Ledger:
    """Append-only record of paid calls made from a work directory."""

    def __init__(self, path: Path):
        self.path = path

    def load(self) -> dict[str, Any]:
        if self.path.exists():
            return json.loads(self.path.read_text(encoding="utf-8"))
        return {"entries": []}

    def spent(self) -> float:
        total = 0.0
        for e in self.load()["entries"]:
            v = e.get("actual_usd")
            total += float(v if v is not None else e.get("estimate_usd", 0.0))
        return round(total, 4)

    def check(self, estimate: float, budget: float | None) -> dict[str, Any]:
        spent = self.spent()
        info: dict[str, Any] = {"spent_usd": spent, "estimate_usd": round(estimate, 4), "budget_usd": budget}
        if budget is not None:
            info["remaining_usd"] = round(budget - spent, 4)
            if spent + estimate > budget + 1e-9:
                raise BudgetError(
                    f"estimated ${estimate:.2f} on top of ${spent:.2f} already spent from this work directory "
                    f"exceeds the budget of ${budget:.2f}",
                    info,
                )
        return info

    def record(self, entry: dict[str, Any]) -> None:
        data = self.load()
        entry = {"time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **entry}
        data["entries"].append(entry)
        write_json_atomic(self.path, data)


def budget_from(arg: float | None) -> float | None:
    if arg is not None:
        return arg
    env = os.environ.get("IMG2FIG_BUDGET_USD")
    return float(env) if env else None
