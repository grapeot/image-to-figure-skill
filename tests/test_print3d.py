"""3D-print repair on synthetic meshes (offline; needs the 'print' extra)."""

import json
import math
from pathlib import Path

import numpy as np
import pytest

trimesh = pytest.importorskip("trimesh")
pytest.importorskip("manifold3d")
pytest.importorskip("skimage")

from image_to_figure import print3d  # noqa: E402
from image_to_figure.print3d import PrintOptions, repair  # noqa: E402

FAST = dict(voxel_mm=0.5, smooth_iterations=5)


def save(mesh, path):
    mesh.export(path)
    return path


def run(tmp_path, mesh, name="m.stl", **kw):
    src = save(mesh, tmp_path / name)
    opts = PrintOptions(**{"up": "z", **FAST, **kw})
    return repair(src, tmp_path / "out", opts)


def figure_like():
    """Body box + head sphere + two legs, standing on z = 0 (z-up, mm-ish units)."""
    body = trimesh.creation.box(extents=(14, 10, 16))
    body.apply_translation((0, 0, 20))
    head = trimesh.creation.icosphere(subdivisions=3, radius=8)
    head.apply_translation((0, 0, 34))
    legs = []
    for x in (-4, 4):
        leg = trimesh.creation.cylinder(radius=2.5, height=12, sections=24)
        leg.apply_translation((x, 0, 6))
        legs.append(leg)
    return trimesh.util.concatenate([body, head, *legs])  # overlapping shells, not a valid solid as is


def test_overlapping_shells_become_one_watertight_part(tmp_path):
    res = run(tmp_path, figure_like(), height_mm=40)
    c = res["checks"]
    assert c["watertight"] and c["is_volume"] and c["printable"]
    assert "NoError" in c["manifold3d_status"]
    assert c["parts"] == 1
    assert res["input"]["watertight"] is True or res["input"]["components"] >= 1
    assert res["output"]["height_mm"] == pytest.approx(40, abs=0.6)
    assert res["output"]["bounds_mm"][0][2] == pytest.approx(0, abs=1e-6)


def test_open_mesh_is_closed(tmp_path):
    sphere = trimesh.creation.icosphere(subdivisions=4, radius=10)
    keep = sphere.face_normals[:, 2] < 0.95  # cut a cap off: an open mesh
    holey = trimesh.Trimesh(sphere.vertices, sphere.faces[keep])
    assert not holey.is_watertight
    res = run(tmp_path, holey, height_mm=20, base="none")
    assert res["checks"]["watertight"]
    vol = res["output"]["volume_mm3"]
    expected = 4 / 3 * math.pi * 10 ** 3 * (20 / 20) ** 3
    assert vol == pytest.approx(expected, rel=0.1)


def test_fragments_merged_removed_kept(tmp_path):
    main = trimesh.creation.box(extents=(20, 20, 20))
    main.apply_translation((0, 0, 10))
    near = trimesh.creation.box(extents=(4, 4, 4))
    near.apply_translation((13.5, 0, 10))  # 1.5 mm gap: wider than the voxel gap closing, inside --merge-gap
    far = trimesh.creation.box(extents=(4, 4, 4))
    far.apply_translation((0, 18, 10))  # 6 mm gap: beyond --merge-gap
    crumb = trimesh.creation.box(extents=(0.6, 0.6, 0.6))
    crumb.apply_translation((-14, 0, 15))
    mesh = trimesh.util.concatenate([main, near, far, crumb])

    res = run(tmp_path, mesh, height_mm=20, base="none", merge_gap_mm=2.0, voxel_mm=0.25)
    step = next(s for s in res["steps"] if s["step"] == "fragments")
    assert step["merged"] == 1 and step["removed"] == 1 and step["kept"] == 1  # far part kept, crumb removed
    assert res["checks"]["parts"] == 2
    assert any("kept as a loose piece" in w for w in res["warnings"])

    res = run(tmp_path, mesh, height_mm=20, base="none", fragments="remove", voxel_mm=0.25)
    assert res["checks"]["parts"] == 1

    res = run(tmp_path, mesh, height_mm=20, base="none", fragments="keep", voxel_mm=0.25)
    step = next(s for s in res["steps"] if s["step"] == "fragments")
    assert step["kept"] == 2 and step["removed"] == 1  # the crumb is under --min-fragment
    assert res["checks"]["parts"] == 3
    assert any("loose piece" in w for w in res["warnings"])


def test_thin_plate_flagged_and_thickened(tmp_path):
    base = trimesh.creation.box(extents=(20, 20, 6))
    base.apply_translation((0, 0, 3))
    fin = trimesh.creation.box(extents=(0.6, 16, 14))  # a 0.6 mm fin
    fin.apply_translation((0, 0, 13))
    mesh = trimesh.util.concatenate([base, fin])
    kw = dict(height_mm=20, base="none", voxel_mm=0.2, min_wall_mm=1.2)
    res = run(tmp_path, mesh, **kw)
    thin = res["checks"]["wall_thickness"]
    assert thin["thin_volume_fraction"] > 0.02
    assert any("thinner than" in w for w in res["warnings"])
    res2 = run(tmp_path, mesh, thicken=True, **kw)
    assert res2["output"]["volume_mm3"] > res["output"]["volume_mm3"]
    assert res2["checks"]["wall_thickness"]["thin_volume_fraction"] < thin["thin_volume_fraction"] / 2


def test_hollow_with_drain_hole(tmp_path):
    cube = trimesh.creation.box(extents=(30, 30, 30))
    cube.apply_translation((0, 0, 15))
    solid = run(tmp_path, cube, height_mm=30, base="flat")
    hollow = run(tmp_path, cube, height_mm=30, base="flat", hollow_wall_mm=2.0, drain_hole_mm=4.0)
    step = next(s for s in hollow["steps"] if s["step"] == "hollow")
    assert step["cavities"] == 1 and len(step["drain_holes"]) == 1
    assert hollow["checks"]["printable"] and hollow["checks"]["parts"] == 1  # cavity open through the hole
    outer = 30 * 30 * (30 - 0.3)
    assert hollow["output"]["volume_mm3"] < 0.4 * outer  # most material removed
    assert solid["output"]["volume_mm3"] == pytest.approx(outer, rel=0.05)


def test_hollow_wall_below_two_voxels_refused(tmp_path):
    cube = trimesh.creation.box(extents=(10, 10, 10))
    with pytest.raises(print3d.UsageError):
        run(tmp_path, cube, height_mm=10, hollow_wall_mm=0.6)


def test_pedestal_adds_height_and_flat_bottom(tmp_path):
    res = run(tmp_path, figure_like(), height_mm=40, base="pedestal", pedestal_height_mm=3.0)
    assert res["output"]["height_mm"] == pytest.approx(43, abs=0.8)
    assert res["output"]["pedestal_height_mm"] == 3.0
    m = trimesh.load(res["output"]["files"]["stl"])
    bottom = m.vertices[:, 2] < 1e-3
    assert bottom.sum() > 20  # a flat face on the bed
    step = next(s for s in res["steps"] if s["step"] == "pedestal")
    assert step["radius_mm"] > 6


def test_gltf_up_axis_and_rotation(tmp_path):
    box = trimesh.creation.box(extents=(10, 30, 4))  # tallest along Y, as a glTF figure would be
    src = save(box, tmp_path / "m.glb")
    res = repair(src, tmp_path / "out", PrintOptions(height_mm=30, base="none", **FAST))
    size = res["output"]["size_mm"]
    assert size[2] == pytest.approx(30, abs=0.6) and size[0] == pytest.approx(10, abs=0.8)
    res = repair(src, tmp_path / "out2", PrintOptions(height_mm=30, base="none", rot_deg=90, **FAST))
    assert res["output"]["size_mm"][1] == pytest.approx(10, abs=0.8)


def test_exports_load_back(tmp_path):
    res = run(tmp_path, figure_like(), height_mm=40)
    files = res["output"]["files"]
    assert trimesh.load(files["stl"]).is_watertight
    if "3mf" in files:
        loaded = trimesh.load(files["3mf"])
        geom = loaded.to_geometry() if isinstance(loaded, trimesh.Scene) else loaded
        assert len(geom.faces) > 100


def test_section_image(tmp_path):
    cube = trimesh.creation.box(extents=(20, 20, 20))
    cube.apply_translation((0, 0, 10))
    res = run(tmp_path, cube, height_mm=20, hollow_wall_mm=2.0)
    out = print3d.section_image(Path(res["output"]["files"]["stl"]), tmp_path / "sec.png")
    from PIL import Image

    assert out["loops"] >= 1
    img = np.asarray(Image.open(out["section"]).convert("L"))
    assert (img < 128).mean() < 0.5  # mostly hollow: the cavity shows as background


def test_cli_print(run_cli, tmp_path):
    src = save(figure_like(), tmp_path / "fig.stl")
    code, out = run_cli("print", "--workdir", tmp_path / "w", "--model", src, "--up", "z", "--height", "40",
                        "--voxel", "0.5", "--smooth", "3", "--no-preview")
    assert code == 0, out
    assert out["checks"]["printable"]
    report = json.loads((tmp_path / "w" / "print" / "figure_report.json").read_text())
    assert report["output"]["files"]["stl"].endswith("figure.stl")


def test_cli_print_needs_model(run_cli, tmp_path):
    code, out = run_cli("print", "--workdir", tmp_path / "w")
    assert code == 2


def test_cli_print_missing_extra(run_cli, tmp_path, monkeypatch):
    from image_to_figure.errors import MissingDependency

    def no_deps():
        raise MissingDependency("print repair needs the 'print' extra")

    monkeypatch.setattr(print3d, "_deps", no_deps)
    src = save(figure_like(), tmp_path / "fig.stl")
    code, out = run_cli("print", "--workdir", tmp_path / "w", "--model", src, "--no-preview")
    assert code == 4

