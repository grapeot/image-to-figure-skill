"""Turn a raw image-to-3D GLB into the figure master.

Usage: blender -b -P prep.py -- --model M.glb --out-blend F.blend --out-glb F_clean.glb --result R.json
       [--rot DEG] [--faces N] [--grade matte|light|dark|none] [--keep-gloss T]
       [--blush X,Z ...] [--blush-size S] [--blush-color R,G,B]

Steps: join meshes, bake the rotation about Z (so the figure faces -Y), normalise height, centre, feet on z = 0,
decimate to at most N faces, export the clean GLB (geometry and original textures, no grade), then apply the material
grade and optional blush and save the .blend with packed textures.

Blush points are given in figure fractions of the front view: X is horizontal offset from the centre line (positive is
the viewer's right, which is the figure's left), Z is height above the feet, both divided by the figure height.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bpy  # noqa: E402
import common  # noqa: E402

GRADES = {
    # name: (saturation, extra tone-curve points or None)
    "matte": (1.15, None),
    "light": (1.4, [(0.20, 0.13), (0.50, 0.47), (0.85, 0.91)]),  # whites pinned, a little depth in folds
    "dark": (1.25, [(0.18, 0.07), (0.40, 0.26), (0.65, 0.62)]),  # deepens dark cloth toward charcoal
}
ROUGH_MIN, ROUGH_MAX = 0.55, 0.8
SPECULAR = 0.35


def build(p):
    p.add_argument("--model", required=True)
    p.add_argument("--out-blend", required=True)
    p.add_argument("--out-glb")
    p.add_argument("--rot", type=float, default=0.0)
    p.add_argument("--faces", type=int, default=350_000)
    p.add_argument("--grade", default="matte", choices=["matte", "light", "dark", "none"])
    p.add_argument("--keep-gloss", type=float, default=0.0)
    p.add_argument("--blush", action="append", default=[])
    p.add_argument("--blush-size", type=float, default=1.0)
    p.add_argument("--blush-color", default="1.0,0.50,0.56")


def link_or_value(nt, sock, kind):
    """Return an output socket that carries what currently feeds `sock`, unlinking it."""
    if sock.links:
        src = sock.links[0].from_socket
        nt.links.remove(sock.links[0])
        return src
    if kind == "color":
        n = nt.nodes.new("ShaderNodeRGB")
        n.outputs[0].default_value = tuple(sock.default_value)
    else:
        n = nt.nodes.new("ShaderNodeValue")
        n.outputs[0].default_value = float(sock.default_value)
    return n.outputs[0]


def grade_material(mat, grade, keep_gloss, blush_mask_builder, blush_rgb):
    nt = mat.node_tree
    bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        return False
    sat, pts = GRADES[grade]
    col = link_or_value(nt, bsdf.inputs["Base Color"], "color")
    hs = nt.nodes.new("ShaderNodeHueSaturation")
    hs.inputs["Saturation"].default_value = sat
    nt.links.new(col, hs.inputs["Color"])
    out = hs.outputs["Color"]
    if pts:
        curve = nt.nodes.new("ShaderNodeRGBCurve")
        c = curve.mapping.curves[3]
        for x, y in pts:
            c.points.new(x, y)
        curve.mapping.update()
        nt.links.new(out, curve.inputs["Color"])
        out = curve.outputs["Color"]
    mask = blush_mask_builder(nt)
    if mask is not None:
        mix = nt.nodes.new("ShaderNodeMix")
        mix.data_type = "RGBA"
        mix.blend_type = "MULTIPLY"
        common.socket(mix.inputs, "B_Color").default_value = blush_rgb + (1.0,)
        nt.links.new(mask, common.socket(mix.inputs, "Factor_Float", 0))
        nt.links.new(out, common.socket(mix.inputs, "A_Color"))
        out = common.socket(mix.outputs, "Result_Color")
    nt.links.new(out, bsdf.inputs["Base Color"])

    rough = link_or_value(nt, bsdf.inputs["Roughness"], "value")
    rm = nt.nodes.new("ShaderNodeMapRange")
    rm.inputs["To Min"].default_value = ROUGH_MIN
    rm.inputs["To Max"].default_value = ROUGH_MAX
    nt.links.new(rough, rm.inputs["Value"])
    metal = link_or_value(nt, bsdf.inputs["Metallic"], "value")
    if keep_gloss > 0:
        # parts that were glossy in the source (roughness below the threshold) keep their roughness and metal
        glossy = nt.nodes.new("ShaderNodeMath")
        glossy.operation = "LESS_THAN"
        glossy.inputs[1].default_value = keep_gloss
        nt.links.new(rough, glossy.inputs[0])
        mixr = nt.nodes.new("ShaderNodeMix")
        mixr.data_type = "FLOAT"
        nt.links.new(glossy.outputs[0], common.socket(mixr.inputs, "Factor_Float", 0))
        nt.links.new(rm.outputs["Result"], common.socket(mixr.inputs, "A_Float"))
        nt.links.new(rough, common.socket(mixr.inputs, "B_Float"))
        nt.links.new(common.socket(mixr.outputs, "Result_Float"), bsdf.inputs["Roughness"])
        mm = nt.nodes.new("ShaderNodeMath")
        mm.operation = "MULTIPLY"
        nt.links.new(metal, mm.inputs[0])
        nt.links.new(glossy.outputs[0], mm.inputs[1])
        nt.links.new(mm.outputs[0], bsdf.inputs["Metallic"])
        spec = nt.nodes.new("ShaderNodeMapRange")  # 0.35 on matte parts, 0.5 on glossy parts
        spec.inputs["To Min"].default_value = SPECULAR
        spec.inputs["To Max"].default_value = 0.5
        nt.links.new(glossy.outputs[0], spec.inputs["Value"])
        nt.links.new(spec.outputs["Result"], bsdf.inputs["Specular IOR Level"])
    else:
        nt.links.new(rm.outputs["Result"], bsdf.inputs["Roughness"])
        bsdf.inputs["Metallic"].default_value = 0.0
        bsdf.inputs["Specular IOR Level"].default_value = SPECULAR
    bsdf.inputs["Subsurface Weight"].default_value = 0.08
    bsdf.inputs["Subsurface Radius"].default_value = (0.9, 0.5, 0.4)
    bsdf.inputs["Subsurface Scale"].default_value = 0.02
    return True


def main():
    a = common.parse(build)
    warnings = []
    common.reset()
    common.import_model(os.path.abspath(a.model))
    obj = common.single_mesh("figure")
    info = common.normalise(obj, a.rot)

    n0 = len(obj.data.polygons)
    if n0 > a.faces:
        dec = obj.modifiers.new("decimate", "DECIMATE")
        dec.ratio = a.faces / n0
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.modifier_apply(modifier="decimate")
    n1 = len(obj.data.polygons)

    if a.out_glb:
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        kw = dict(filepath=os.path.abspath(a.out_glb), use_selection=True, export_format="GLB",
                  export_image_format="JPEG")
        try:
            bpy.ops.export_scene.gltf(**kw, export_image_quality=90)
        except TypeError:  # older exporters
            bpy.ops.export_scene.gltf(**kw, export_jpeg_quality=90)

    H = common.HEIGHT
    blush_pts = []
    if a.blush:
        surf = common.Surface(obj)
        for spec in a.blush:
            x, z = (float(v) for v in spec.split(","))
            loc, _ = surf.front(x * H, z * H)
            if loc is None:
                warnings.append(f"blush point {spec} missed the surface; skipped")
                continue
            blush_pts.append(tuple(loc))
    size = a.blush_size
    blush_rgb = tuple(float(v) for v in a.blush_color.split(","))

    def mask_builder(nt):
        if not blush_pts:
            return None
        tc = nt.nodes.new("ShaderNodeTexCoord")
        mask = None
        for p in blush_pts:
            sub = nt.nodes.new("ShaderNodeVectorMath")
            sub.operation = "SUBTRACT"
            sub.inputs[1].default_value = p
            sc = nt.nodes.new("ShaderNodeVectorMath")
            sc.operation = "MULTIPLY"
            sc.inputs[1].default_value = (1 / (0.12 * size), 1 / (0.06 * size), 1 / (0.075 * size))
            ln = nt.nodes.new("ShaderNodeVectorMath")
            ln.operation = "LENGTH"
            mr = nt.nodes.new("ShaderNodeMapRange")
            mr.clamp = True
            mr.inputs["To Min"].default_value = 0.85
            mr.inputs["To Max"].default_value = 0.0
            nt.links.new(tc.outputs["Object"], sub.inputs[0])
            nt.links.new(sub.outputs[0], sc.inputs[0])
            nt.links.new(sc.outputs[0], ln.inputs[0])
            nt.links.new(ln.outputs["Value"], mr.inputs["Value"])
            if mask is None:
                mask = mr.outputs["Result"]
            else:
                add = nt.nodes.new("ShaderNodeMath")
                add.operation = "ADD"
                add.use_clamp = True
                nt.links.new(mask, add.inputs[0])
                nt.links.new(mr.outputs["Result"], add.inputs[1])
                mask = add.outputs[0]
        return mask

    graded = []
    if a.grade != "none":
        common.ensure_material(obj)
        for mat in obj.data.materials:
            if mat is None:
                continue
            mat.use_nodes = True
            if grade_material(mat, a.grade, a.keep_gloss, mask_builder, blush_rgb):
                graded.append(mat.name)
            else:
                warnings.append(f"material {mat.name} has no Principled BSDF; left as is")
    elif blush_pts:
        warnings.append("--grade none: blush points ignored")

    for im in bpy.data.images:
        if im.size[0] > 0 and not im.packed_file:
            im.pack()
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(a.out_blend))
    lo, hi = common.bounds(obj)
    common.write_result(a.result, {
        "blend": os.path.abspath(a.out_blend),
        "glb": os.path.abspath(a.out_glb) if a.out_glb else None,
        "rotation_deg": a.rot,
        "faces_in": n0,
        "faces_out": n1,
        "height_units": H,
        "dimensions_units": [round(hi[i] - lo[i], 4) for i in range(3)],
        "grade": a.grade,
        "keep_gloss": a.keep_gloss,
        "graded_materials": graded,
        "blush_points_world": [[round(c, 4) for c in p] for p in blush_pts],
        "warnings": warnings,
        **info,
    })


main()
