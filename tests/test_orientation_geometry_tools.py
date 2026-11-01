import ast
import stat
import textwrap

import pytest
from PIL import Image, ImageDraw

from image_to_figure import blender as blender_mod
from image_to_figure import blender_geometry, orientation, video
from image_to_figure.errors import MissingDependency, ToolError

SKIN, DARK, BG = (245, 205, 185), (30, 30, 70), (200, 200, 205)


def figure_image(path, facing, rgba=False, size=(300, 400)):
    """Head + body. facing: 'front' (skin face disc), 'back' (all dark), 'side' (narrow, half skin)."""
    mode = "RGBA" if rgba else "RGB"
    im = Image.new(mode, size, (0, 0, 0, 0) if rgba else BG)
    d = ImageDraw.Draw(im)
    w, h = size
    half = 70 if facing in ("front", "back") else 40
    d.rectangle((w // 2 - half, h // 2, w // 2 + half, h - 20), fill=DARK)
    d.ellipse((w // 2 - half, 40, w // 2 + half, h // 2 + 10), fill=DARK)
    if facing == "front":
        d.ellipse((w // 2 - half + 15, 70, w // 2 + half - 15, h // 2), fill=SKIN)
    if facing == "side":
        d.chord((w // 2 - half, 40, w // 2 + half, h // 2 + 10), 90, 270, fill=SKIN)
    im.save(path)
    return path


def test_choose_picks_the_face(tmp_path):
    ref = figure_image(tmp_path / "view_front.png", "front")
    renders = {
        "0": str(figure_image(tmp_path / "r0.png", "side", rgba=True)),
        "90": str(figure_image(tmp_path / "r90.png", "back", rgba=True)),
        "180": str(figure_image(tmp_path / "r180.png", "side", rgba=True)),
        "270": str(figure_image(tmp_path / "r270.png", "front", rgba=True)),
    }
    res = orientation.choose(ref, renders, hint=None)
    assert res["rotation_deg"] == 270.0
    assert not res.get("ambiguous")
    assert res["scores"]["270"]["iou"] > 0.9


def test_ambiguous_falls_back_to_hint(tmp_path):
    ref = figure_image(tmp_path / "view_front.png", "back")
    renders = {k: str(figure_image(tmp_path / f"r{k}.png", "back", rgba=True)) for k in ("0", "90")}
    res = orientation.choose(ref, renders, hint=-90.0)
    assert res["ambiguous"] is True
    assert res["rotation_deg"] == 270.0


def test_pixel_to_figure_centre_and_scale():
    out = blender_geometry.pixel_to_figure(450, 600)
    assert out["x"] == 0
    assert out["z"] == pytest.approx(1.45 / 2.9, abs=1e-4)
    top = blender_geometry.pixel_to_figure(450, 0)
    assert top["z"] == pytest.approx((1.45 + 600 * 3.3 / 1200) / 2.9, abs=1e-4)


def test_geometry_constants_match_blender_side():
    src = (blender_mod.SCRIPTS / "common.py").read_text()
    tree = ast.parse(src)
    vals = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in ("HEIGHT", "ORTHO_SCALE", "CAMERAS"):
                vals[name] = ast.literal_eval(node.value)
    assert vals["HEIGHT"] == blender_geometry.HEIGHT
    assert vals["ORTHO_SCALE"] == blender_geometry.ORTHO_SCALE
    assert vals["CAMERAS"]["front"][0][2] == blender_geometry.FRONT_CAM_Z
    assert vals["CAMERAS"]["front"][2] == "ortho"


def test_ffmpeg_command():
    from pathlib import Path

    cmd = video.encode_cmd("ffmpeg", Path("/frames"), "f_%04d.png", 32, Path("/out.mp4"))
    assert cmd[cmd.index("-framerate") + 1] == "32"
    assert "yuv420p" in cmd and cmd[-1] == "/out.mp4"


def test_missing_ffmpeg(monkeypatch, tmp_path):
    monkeypatch.setattr(video, "find_ffmpeg", lambda: None)
    with pytest.raises(MissingDependency):
        video.encode(tmp_path, "f_%04d.png", 24, tmp_path / "o.mp4", tmp_path / "l.log")


def _fake_blender(tmp_path, body):
    exe = tmp_path / "fake_blender"
    exe.write_text("#!/usr/bin/env python3\n" + textwrap.dedent(body))
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return exe


def test_runner_reads_result(tmp_path, monkeypatch):
    exe = _fake_blender(tmp_path, """
        import json, sys
        out = sys.argv[sys.argv.index("--result") + 1]
        json.dump({"args": sys.argv[sys.argv.index("--") + 1:]}, open(out, "w"))
    """)
    monkeypatch.setenv("IMG2FIG_BLENDER", str(exe))
    res = blender_mod.run_script("render.py", ["--input", "x.blend"], tmp_path / "logs" / "r.log")
    assert res["args"][:2] == ["--input", "x.blend"]
    assert (tmp_path / "logs" / "r.log").exists()


def test_runner_failure_passes_stderr(tmp_path, monkeypatch):
    exe = _fake_blender(tmp_path, """
        import sys
        sys.stderr.write("Traceback: KeyError 'Base Color'\\n")
        sys.exit(1)
    """)
    monkeypatch.setenv("IMG2FIG_BLENDER", str(exe))
    with pytest.raises(ToolError) as e:
        blender_mod.run_script("prep.py", [], tmp_path / "p.log")
    assert e.value.exit_code == 5
    assert e.value.detail["exit_code"] == 1
    assert "KeyError" in e.value.detail["stderr_tail"]


def test_missing_blender(monkeypatch, tmp_path):
    monkeypatch.setenv("IMG2FIG_BLENDER", str(tmp_path / "nope"))
    with pytest.raises(MissingDependency):
        blender_mod.run_script("prep.py", [], tmp_path / "p.log")


def test_blender_scripts_do_not_import_the_package():
    for f in blender_mod.SCRIPTS.glob("*.py"):
        tree = ast.parse(f.read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                assert not any(n.startswith("image_to_figure") for n in names), f.name

