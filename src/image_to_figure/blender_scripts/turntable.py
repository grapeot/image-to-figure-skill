"""360-degree turntable frames of a figure, optionally on a clear acrylic display base.

Usage: blender -b -P turntable.py -- --input F.blend|M.glb --out-dir DIR --result R.json
       [--frames 192] [--res 854x480] [--no-base] [--samples 32] [--view-transform agx|standard] [--rot DEG]
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bpy  # noqa: E402
import common  # noqa: E402


def build(p):
    p.add_argument("--input", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--frames", type=int, default=192)
    p.add_argument("--res", default="854x480")
    p.add_argument("--no-base", action="store_true")
    p.add_argument("--samples", type=int, default=32)
    p.add_argument("--view-transform", default="agx", choices=["agx", "standard"])
    p.add_argument("--rot", type=float, default=0.0)


def acrylic():
    m = bpy.data.materials.new("acrylic")
    m.use_nodes = True
    b = next(n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    b.inputs["Base Color"].default_value = (0.92, 0.92, 1.0, 1.0)
    b.inputs["Roughness"].default_value = 0.04
    b.inputs["IOR"].default_value = 1.49
    b.inputs["Transmission Weight"].default_value = 1.0
    return m


def flat_material(name, rgb, rough):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = next(n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    b.inputs["Base Color"].default_value = common.srgb_to_lin(rgb) + (1.0,)
    b.inputs["Roughness"].default_value = rough
    return m


a = common.parse(build)
obj, _ = common.load_figure(os.path.abspath(a.input), a.rot)
common.ensure_material(obj)
w, h = (int(v) for v in a.res.split("x"))
sc = common.studio(view_transform=a.view_transform, res=(w, h))
bpy.data.worlds["world"].node_tree.nodes["Background"].inputs["Strength"].default_value = 0.6
pivot = bpy.data.objects.new("turntable", None)
sc.collection.objects.link(pivot)
obj.parent = pivot

bpy.ops.mesh.primitive_circle_add(vertices=128, radius=40, fill_type="NGON", location=(0, 0, -0.06))
floor = bpy.context.active_object
floor.data.materials.append(flat_material("floor", common.BG, 0.9))
if not a.no_base:
    bpy.ops.mesh.primitive_cylinder_add(vertices=96, radius=0.95, depth=0.06, location=(0, 0, -0.03))
    base = bpy.context.active_object
    base.data.materials.append(acrylic())
    for poly in base.data.polygons:
        poly.use_smooth = True
    bev = base.modifiers.new("bevel", "BEVEL")
    bev.width = 0.015
    bev.segments = 4
    bev.limit_method = "ANGLE"
    base.parent = pivot

cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
sc.collection.objects.link(cam)
cam.location = (0, -8.8, 2.2)
common.look_at(cam, (0, 0, 1.4))
cam.data.lens = 50
sc.camera = cam

pivot.rotation_mode = "XYZ"
n = a.frames
for f, ang in ((1, 0.0), (n + 1, 2 * math.pi)):
    pivot.rotation_euler = (0, 0, ang)
    pivot.keyframe_insert("rotation_euler", frame=f)
action = pivot.animation_data.action
fcurves = list(getattr(action, "fcurves", []) or [])
if not fcurves:  # Blender 5 layered actions keep curves in channel bags
    for layer in getattr(action, "layers", []):
        for strip in layer.strips:
            for bag in getattr(strip, "channelbags", []):
                fcurves.extend(bag.fcurves)
for fc in fcurves:
    for kp in fc.keyframe_points:
        kp.interpolation = "LINEAR"
sc.frame_start, sc.frame_end = 1, n
if hasattr(sc.eevee, "taa_render_samples"):
    sc.eevee.taa_render_samples = a.samples
os.makedirs(a.out_dir, exist_ok=True)
sc.render.image_settings.file_format = "PNG"
sc.render.filepath = os.path.join(os.path.abspath(a.out_dir), "f_")
bpy.ops.render.render(animation=True)
common.write_result(a.result, {
    "frames_dir": os.path.abspath(a.out_dir),
    "pattern": "f_%04d.png",
    "frames": n,
    "linear_interpolation_set": bool(fcurves),
})
