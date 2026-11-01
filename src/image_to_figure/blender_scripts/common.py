"""Helpers shared by the scripts that run inside Blender (`blender -b -P <script> -- ...`).

These modules run in Blender's own Python. They must not import `image_to_figure`.

Conventions: the figure is normalised to HEIGHT units tall, feet on z = 0, centred on x/y, facing -Y. The figure's left
side is +X, so the "left" view camera sits on +X and the "right" view camera on -X.
"""

import argparse
import json
import math
import os
import sys

import bmesh
import bpy
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

HEIGHT = 2.9

# view -> (camera location, look-at target, lens in mm or "ortho")
CAMERAS = {
    "front": ((0.0, -9.5, 1.45), (0.0, 0.0, 1.45), "ortho"),
    "left": ((9.5, 0.0, 1.45), (0.0, 0.0, 1.45), "ortho"),
    "back": ((0.0, 9.5, 1.45), (0.0, 0.0, 1.45), "ortho"),
    "right": ((-9.5, 0.0, 1.45), (0.0, 0.0, 1.45), "ortho"),
    "q34": ((3.6, -5.4, 1.9), (0.0, 0.0, 1.40), 50),
    "face": ((0.0, -4.2, 2.0), (0.0, 0.0, 1.92), 60),
}
ORTHO_SCALE = 3.3
FIGURE_RES = (900, 1200)
BG = (0.80, 0.74, 0.92)  # soft lavender, sRGB


def parse(build):
    """argparse over the arguments after `--`. `build(parser)` adds the script's own options."""
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--result", required=True)
    build(p)
    return p.parse_args(argv)


def write_result(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def srgb_to_lin(c):
    return tuple((x / 12.92) if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c)


def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def import_model(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=path)
    elif ext == ".obj":
        bpy.ops.wm.obj_import(filepath=path)
    elif ext == ".stl":
        bpy.ops.wm.stl_import(filepath=path)
    elif ext == ".ply":
        bpy.ops.wm.ply_import(filepath=path)
    elif ext == ".fbx":
        bpy.ops.import_scene.fbx(filepath=path)
    else:
        raise SystemExit(f"unsupported model format: {ext}")
    return [o for o in bpy.context.scene.objects if o.type == "MESH"]


def single_mesh(name="figure"):
    """Bake every mesh's world transform into its data, delete everything else, join into one object."""
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if not meshes:
        raise SystemExit("no mesh objects in the input")
    for o in meshes:
        if o.data.users > 1:
            o.data = o.data.copy()
        o.data.transform(o.matrix_world)
    for o in meshes:
        o.parent = None
        o.matrix_world = Matrix.Identity(4)
    for o in list(bpy.context.scene.objects):
        if o.type != "MESH":
            bpy.data.objects.remove(o, do_unlink=True)
    bpy.ops.object.select_all(action="DESELECT")
    for o in meshes:
        o.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    if len(meshes) > 1:
        bpy.ops.object.join()
    obj = bpy.context.view_layer.objects.active
    obj.name = name
    return obj


def bounds(obj):
    vs = obj.data.vertices
    xs = [v.co.x for v in vs]
    ys = [v.co.y for v in vs]
    zs = [v.co.z for v in vs]
    return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))


def normalise(obj, rot_deg=0.0, height=HEIGHT):
    """Rotate about Z, scale to `height`, centre on x/y, feet on z = 0. Returns raw dimensions and scale."""
    lo, hi = bounds(obj)
    raw = [hi[i] - lo[i] for i in range(3)]
    if rot_deg:
        obj.data.transform(Matrix.Rotation(math.radians(rot_deg), 4, "Z"))
    lo, hi = bounds(obj)
    s = height / max(hi[2] - lo[2], 1e-9)
    obj.data.transform(Matrix.Scale(s, 4))
    lo, hi = bounds(obj)
    obj.data.transform(Matrix.Translation((-(lo[0] + hi[0]) / 2, -(lo[1] + hi[1]) / 2, -lo[2])))
    obj.data.update()
    return {"raw_dimensions": [round(v, 5) for v in raw], "scale": s}


def ensure_material(obj):
    if not obj.data.materials or all(m is None for m in obj.data.materials):
        m = bpy.data.materials.new("vinyl_grey")
        m.use_nodes = True
        b = next(n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
        b.inputs["Base Color"].default_value = srgb_to_lin((0.86, 0.85, 0.84)) + (1.0,)
        b.inputs["Roughness"].default_value = 0.6
        obj.data.materials.clear()
        obj.data.materials.append(m)


def set_engine_eevee(scene):
    for engine in ("BLENDER_EEVEE", "BLENDER_EEVEE_NEXT"):
        try:
            scene.render.engine = engine
            return
        except TypeError:
            continue


def look_at(obj, target):
    d = Vector(target) - obj.location
    obj.rotation_mode = "QUATERNION"
    obj.rotation_quaternion = d.to_track_quat("-Z", "Y")


def area_light(name, loc, target, power, size=2.0, color=(1, 1, 1)):
    ld = bpy.data.lights.new(name, "AREA")
    ld.energy = power
    ld.size = size
    ld.color = color
    lo = bpy.data.objects.new(name, ld)
    bpy.context.collection.objects.link(lo)
    lo.location = loc
    look_at(lo, target)
    return lo


def camera(view, name=None):
    loc, tgt, lens = CAMERAS[view]
    cd = bpy.data.cameras.new(name or "cam_" + view)
    if lens == "ortho":
        cd.type = "ORTHO"
        cd.ortho_scale = ORTHO_SCALE
    else:
        cd.lens = lens
    c = bpy.data.objects.new(name or "cam_" + view, cd)
    bpy.context.collection.objects.link(c)
    c.location = loc
    look_at(c, tgt)
    bpy.context.scene.camera = c
    return c


def studio(view_transform="agx", res=FIGURE_RES, bg=BG, exposure=0.15):
    """Soft three-point figure studio on a plain background (EEVEE)."""
    sc = bpy.context.scene
    set_engine_eevee(sc)
    sc.render.resolution_x, sc.render.resolution_y = res
    sc.render.resolution_percentage = 100
    sc.render.film_transparent = False
    if view_transform == "standard":
        sc.view_settings.view_transform = "Standard"
        sc.view_settings.look = "None"
        sc.view_settings.exposure = 0.0
    else:
        sc.view_settings.view_transform = "AgX"
        try:
            sc.view_settings.look = "AgX - Base Contrast"
        except TypeError:
            pass
        sc.view_settings.exposure = exposure
    ee = sc.eevee
    for attr, val in (("use_raytracing", True), ("use_shadows", True), ("taa_render_samples", 64),
                      ("use_gtao", True), ("fast_gi_method", "GLOBAL_ILLUMINATION")):
        if hasattr(ee, attr):
            try:
                setattr(ee, attr, val)
            except (TypeError, AttributeError):
                pass
    w = bpy.data.worlds.new("world")
    sc.world = w
    w.use_nodes = True
    bgn = w.node_tree.nodes.get("Background")
    bgn.inputs["Color"].default_value = srgb_to_lin(bg) + (1,)
    bgn.inputs["Strength"].default_value = 0.45
    t = Vector((0, 0, 1.4))
    s = 1.6
    area_light("key", t + Vector((-2.5, -3.5, 3.5)) * s, t, 600 * s * s * 1.5, size=3 * s, color=(1.0, 0.92, 0.88))
    area_light("fill", t + Vector((3.5, -2.5, 1.5)) * s, t, 250 * s * s, size=4 * s, color=(0.95, 0.95, 1.0))
    area_light("rim", t + Vector((0.5, 4.0, 3.0)) * s, t, 400 * s * s, size=3 * s, color=(1.0, 0.95, 1.0))
    area_light("front", Vector((0.6, -6, 2.4)), Vector((0, 0, 1.6)), 500, size=4, color=(1.0, 0.95, 0.93))
    return sc


def clear_non_mesh():
    for o in list(bpy.context.scene.objects):
        if o.type != "MESH":
            bpy.data.objects.remove(o, do_unlink=True)


def render(path):
    bpy.context.scene.render.filepath = path
    bpy.context.scene.render.image_settings.file_format = "PNG"
    bpy.ops.render.render(write_still=True)


def load_figure(path, rot=0.0):
    """Open a .blend (already normalised by prep) or import and normalise a model file. Returns the figure object."""
    if path.lower().endswith(".blend"):
        bpy.ops.wm.open_mainfile(filepath=path)
        clear_non_mesh()
        meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
        if not meshes:
            raise SystemExit("the .blend contains no mesh")
        obj = meshes[0] if len(meshes) == 1 else single_mesh()
        if rot:
            obj.data.transform(Matrix.Rotation(math.radians(rot), 4, "Z"))
        return obj, None
    reset()
    import_model(path)
    obj = single_mesh()
    info = normalise(obj, rot)
    ensure_material(obj)
    return obj, info


class Surface:
    """Ray casts against an object's evaluated mesh in world space."""

    def __init__(self, obj):
        dg = bpy.context.evaluated_depsgraph_get()
        ev = obj.evaluated_get(dg)
        me = ev.to_mesh()
        bm = bmesh.new()
        bm.from_mesh(me)
        bm.transform(obj.matrix_world)
        self.tree = BVHTree.FromBMesh(bm)
        bm.free()
        ev.to_mesh_clear()

    def front(self, x, z, y0=-10.0):
        loc, nrm, _, _ = self.tree.ray_cast(Vector((x, y0, z)), Vector((0, 1, 0)))
        return loc, nrm


def socket(sockets, identifier, fallback_index=None):
    """Pick a node socket by identifier (names such as "A" are ambiguous on Mix nodes)."""
    for s in sockets:
        if s.identifier == identifier:
            return s
    if fallback_index is not None:
        return sockets[fallback_index]
    raise KeyError(identifier)
