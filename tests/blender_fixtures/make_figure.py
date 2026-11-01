"""Build a small synthetic 'figure' GLB for tests: body, head, a face patch and a nose on one side.

Usage: blender -b --factory-startup -P make_figure.py -- OUT.glb [--facing +x|-y] [--texture]

The face side gets a light skin colour, everything else a dark hair/cloth colour, so front and back differ in colour
the way real figures do. With --texture the colours are baked into an image texture (like provider GLBs) instead of
plain material colours.
"""

import math
import sys

import bmesh
import bpy
from mathutils import Matrix

argv = sys.argv[sys.argv.index("--") + 1:]
out = argv[0]
facing = "+x"
if "--facing" in argv:
    facing = argv[argv.index("--facing") + 1]
textured = "--texture" in argv

bpy.ops.wm.read_factory_settings(use_empty=True)
bm = bmesh.new()
# body: tapered cylinder; head: sphere; nose: small sphere on -Y (front)
bmesh.ops.create_cone(bm, cap_ends=True, segments=32, radius1=0.45, radius2=0.3, depth=1.0,
                      matrix=Matrix.Translation((0, 0, 0.5)))
bmesh.ops.create_uvsphere(bm, u_segments=32, v_segments=16, radius=0.55, matrix=Matrix.Translation((0, 0, 1.45)))
bmesh.ops.create_uvsphere(bm, u_segments=12, v_segments=8, radius=0.12, matrix=Matrix.Translation((0, -0.55, 1.4)))
# arms: two thin cylinders, slightly asymmetric so left/right differ
bmesh.ops.create_cone(bm, cap_ends=True, segments=12, radius1=0.09, radius2=0.09, depth=0.7,
                      matrix=Matrix.Translation((0.55, 0, 0.65)) @ Matrix.Rotation(math.radians(10), 4, "Y"))
bmesh.ops.create_cone(bm, cap_ends=True, segments=12, radius1=0.09, radius2=0.09, depth=0.6,
                      matrix=Matrix.Translation((-0.55, 0, 0.7)) @ Matrix.Rotation(math.radians(-10), 4, "Y"))
me = bpy.data.meshes.new("figure")
bm.to_mesh(me)
bm.free()
obj = bpy.data.objects.new("figure", me)
bpy.context.collection.objects.link(obj)

skin = (0.98, 0.82, 0.72, 1.0)
dark = (0.10, 0.10, 0.25, 1.0)


def is_face(poly):
    c = poly.center
    return poly.normal.y < -0.35 and c.z > 1.0


if textured:
    # 2x1 texture: left half skin, right half dark; UVs pick the half
    img = bpy.data.images.new("tex", 64, 32)
    px = []
    for _y in range(32):
        for x in range(64):
            px.extend(skin if x < 32 else dark)
    img.pixels = px
    img.pack()
    uv = me.uv_layers.new(name="UVMap")
    for poly in me.polygons:
        u = 0.25 if is_face(poly) else 0.75
        for li in poly.loop_indices:
            uv.data[li].uv = (u, 0.5)
    m = bpy.data.materials.new("tex_mat")
    m.use_nodes = True
    nt = m.node_tree
    bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = img
    nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    bsdf.inputs["Roughness"].default_value = 0.2
    me.materials.append(m)
else:
    for name, col in (("skin", skin), ("dark", dark)):
        m = bpy.data.materials.new(name)
        m.use_nodes = True
        b = next(n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
        b.inputs["Base Color"].default_value = col
        b.inputs["Roughness"].default_value = 0.2
        me.materials.append(m)
    for poly in me.polygons:
        poly.material_index = 0 if is_face(poly) else 1

if facing == "+x":  # rotate so the face points along +X, like some provider outputs
    me.transform(Matrix.Rotation(math.radians(90), 4, "Z"))
me.update()
bpy.ops.object.select_all(action="DESELECT")
obj.select_set(True)
bpy.ops.export_scene.gltf(filepath=out, use_selection=True, export_format="GLB")
print("WROTE", out, len(me.polygons), "faces")
