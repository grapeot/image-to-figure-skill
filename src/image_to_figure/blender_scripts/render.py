"""Review renders of a figure from fixed cameras.

Usage: blender -b -P render.py -- --input F.blend|M.glb --out-dir DIR --result R.json
       [--views front,left,back,right,q34,face] [--view-transform agx|standard] [--rot DEG] [--prefix NAME]

A .blend from `prep` is already normalised; a model file is imported and normalised here (with --rot if it does not
face -Y yet). Front, left, back and right are orthographic and match the turnaround views one to one.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common  # noqa: E402


def build(p):
    p.add_argument("--input", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--views", default="front,left,back,right,q34,face")
    p.add_argument("--view-transform", default="agx", choices=["agx", "standard"])
    p.add_argument("--rot", type=float, default=0.0)
    p.add_argument("--prefix", default="")
    p.add_argument("--res", default="900x1200")


a = common.parse(build)
views = [v for v in a.views.split(",") if v]
unknown = [v for v in views if v not in common.CAMERAS]
if unknown:
    raise SystemExit(f"unknown views {unknown}; choose from {sorted(common.CAMERAS)}")
obj, info = common.load_figure(os.path.abspath(a.input), a.rot)
common.ensure_material(obj)
w, h = (int(v) for v in a.res.split("x"))
common.studio(view_transform=a.view_transform, res=(w, h))
os.makedirs(a.out_dir, exist_ok=True)
out = {}
for v in views:
    common.camera(v)
    path = os.path.join(os.path.abspath(a.out_dir), f"{a.prefix}{v}.png")
    common.render(path)
    out[v] = path
lo, hi = common.bounds(obj)
common.write_result(a.result, {
    "renders": out,
    "faces": len(obj.data.polygons),
    "view_transform": a.view_transform,
    "dimensions_units": [round(hi[i] - lo[i], 4) for i in range(3)],
    **(info or {}),
})
