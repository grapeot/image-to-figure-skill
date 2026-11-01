"""End-to-end Blender stages on a synthetic model. Skipped when no Blender binary is found (e.g. in CI)."""

import subprocess
from pathlib import Path

import pytest
from PIL import Image

from image_to_figure import blender as blender_mod
from image_to_figure import video

FIXTURE = Path(__file__).parent / "blender_fixtures" / "make_figure.py"
pytestmark = pytest.mark.blender


def make_glb(tmp_path, facing):
    out = tmp_path / f"fig_{facing.strip('+-')}.glb"
    subprocess.run([blender_mod.find_blender(), "-b", "--factory-startup", "--python-exit-code", "1", "-P",
                    str(FIXTURE), "--", str(out), "--facing", facing, "--texture"],
                   check=True, capture_output=True, timeout=300)
    return out


@pytest.fixture(scope="module")
def models(tmp_path_factory):
    d = tmp_path_factory.mktemp("models")
    return {"y": make_glb(d, "-y"), "x": make_glb(d, "+x")}


def test_prep_finds_orientation_and_renders(run_cli, tmp_path, models):
    wd = tmp_path / "work"
    # reference views: the correctly facing model rendered with the Standard transform
    code, out = run_cli("render", "--workdir", wd, "--input", models["y"], "--views", "front,left,back,right",
                        "--view-transform", "standard", "--out-dir", wd / "views", "--prefix", "view_")
    assert code == 0, out
    # the +X-facing model must be rotated by -90 (= 270) degrees
    code, out = run_cli("prep", "--workdir", wd, "--model", models["x"], "--blush=0.1,0.5", "--blush=-0.1,0.5",
                        "--faces", "800")
    assert code == 0, out
    assert out["orientation"]["rotation_deg"] == 270.0
    assert not out["orientation"].get("ambiguous")
    prep = out["prep"]
    assert prep["faces_out"] <= 800 < prep["faces_in"]
    assert prep["dimensions_units"][2] == pytest.approx(2.9, abs=1e-3)
    assert len(prep["blush_points_world"]) == 2
    assert prep["graded_materials"]
    assert Path(prep["blend"]).exists() and Path(prep["glb"]).exists()
    assert set(out["renders"]) == {"front", "left", "back", "right", "q34", "face"}
    assert Image.open(out["renders"]["front"]).size == (900, 1200)
    assert Path(out["compare"]["sheet"]).exists()


def test_keep_gloss_and_grades_run(run_cli, tmp_path, models):
    wd = tmp_path / "work"
    for grade in ("light", "dark", "none"):
        code, out = run_cli("prep", "--workdir", wd, "--model", models["y"], "--rot", "0", "--grade", grade,
                            "--keep-gloss", "0.3", "--no-render")
        assert code == 0, out
        assert out["prep"]["grade"] == grade


@pytest.mark.skipif(video.find_ffmpeg() is None, reason="no ffmpeg")
def test_turntable(run_cli, tmp_path, models):
    wd = tmp_path / "work"
    code, out = run_cli("prep", "--workdir", wd, "--model", models["y"], "--rot", "0", "--no-render")
    assert code == 0
    code, out = run_cli("turntable", "--workdir", wd, "--frames", "8", "--fps", "8", "--res", "320x180",
                        "--samples", "4")
    assert code == 0, out
    assert out["frames"] == 8 and Path(out["video"]).stat().st_size > 1000
    assert not (wd / "turntable" / "frames").exists()


def test_blender_script_error_is_tool_error(run_cli, tmp_path):
    bad = tmp_path / "broken.glb"
    bad.write_bytes(b"not a glb")
    code, out = run_cli("prep", "--workdir", tmp_path / "w", "--model", bad, "--rot", "0")
    assert code == 5 and out["error"]["type"] == "tool_error"
    assert out["error"]["detail"]["log"]
