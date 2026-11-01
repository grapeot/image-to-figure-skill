"""Render flat-shaded front views of a model at several rotations about Z, for the orientation check.

Usage: blender -b -P orient.py -- --model M.glb --out-dir DIR --result R.json [--rotations 0,90,180,270]

Workbench, flat light, texture colour, Standard view transform and a transparent film, so the PNG colours are the
texture's sRGB colours and the alpha channel is the silhouette. The scoring happens outside Blender.
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bpy  # noqa: E402
import common  # noqa: E402
from mathutils import Matrix  # noqa: E402


def build(p):
    p.add_argument("--model", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--rotations", default="0,90,180,270")
    p.add_argument("--res", default="300x400")


args = common.parse(build)
os.makedirs(args.out_dir, exist_ok=True)
common.reset()
common.import_model(os.path.abspath(args.model))
obj = common.single_mesh()
info = common.normalise(obj, 0.0)
faces = len(obj.data.polygons)

sc = bpy.context.scene
sc.render.engine = "BLENDER_WORKBENCH"
w, h = (int(v) for v in args.res.split("x"))
sc.render.resolution_x, sc.render.resolution_y = w, h
sc.render.film_transparent = True
sc.display.shading.light = "FLAT"
sc.display.shading.color_type = "TEXTURE"
sc.view_settings.view_transform = "Standard"
sc.view_settings.look = "None"
sc.render.image_settings.color_mode = "RGBA"
common.camera("front")

renders = {}
prev = 0.0
for r in [float(v) for v in args.rotations.split(",")]:
    obj.data.transform(Matrix.Rotation(math.radians(r - prev), 4, "Z"))
    prev = r
    path = os.path.join(os.path.abspath(args.out_dir), f"orient_{int(r) % 360:03d}.png")
    common.render(path)
    renders[str(int(r) % 360)] = path

common.write_result(args.result, {"renders": renders, "faces": faces, **info})
