import numpy as np
import pytest
from PIL import Image

from conftest import draw_sheet
from image_to_figure import crop
from image_to_figure.errors import CheckFailed


def test_crop_four_views_equal_size(tmp_path):
    sheet = draw_sheet(tmp_path / "sheet.png")
    info = crop.crop_sheet(sheet, tmp_path / "views")
    assert len(info["runs"]) == 4
    sizes = {tuple(Image.open(v["path"]).size) for v in info["views"].values()}
    assert len(sizes) == 1  # same width and height for every view
    assert set(info["views"]) == {"front", "left", "back", "right"}
    assert info["background_rgb"] == [214, 214, 216]


def test_views_keep_ground_line(tmp_path):
    sheet = draw_sheet(tmp_path / "sheet.png")
    info = crop.crop_sheet(sheet, tmp_path / "views")
    bottoms = []
    for v in info["views"].values():
        a = np.asarray(Image.open(v["path"]).convert("RGB"), dtype=int)
        fg = np.abs(a - np.array(info["background_rgb"])).max(axis=2) > 24
        bottoms.append(np.where(fg.any(axis=1))[0].max())
    assert max(bottoms) - min(bottoms) <= 1


def test_figure_is_centred_in_its_view(tmp_path):
    sheet = draw_sheet(tmp_path / "sheet.png")
    info = crop.crop_sheet(sheet, tmp_path / "views")
    a = np.asarray(Image.open(info["views"]["back"]["path"]).convert("RGB"), dtype=int)
    fg = np.abs(a - np.array(info["background_rgb"])).max(axis=2) > 24
    cols = np.where(fg.any(axis=0))[0]
    centre = (cols.min() + cols.max()) / 2
    assert abs(centre - a.shape[1] / 2) <= 2


def test_touching_figures_fail_with_hint(tmp_path):
    sheet = draw_sheet(tmp_path / "sheet.png", gap_ok=False)
    with pytest.raises(CheckFailed) as e:
        crop.crop_sheet(sheet, tmp_path / "views")
    assert e.value.exit_code == 6
    assert "touch" in e.value.detail["hint"]
    assert e.value.detail["runs"]


def test_wrong_count_reports_runs(tmp_path):
    sheet = draw_sheet(tmp_path / "sheet.png", n=5)
    with pytest.raises(CheckFailed) as e:
        crop.crop_sheet(sheet, tmp_path / "views")
    assert len(e.value.detail["runs"]) == 5
    info = crop.crop_sheet(sheet, tmp_path / "v5", expected=5, names=("a", "b", "c", "d", "e"))
    assert len(info["views"]) == 5


def test_mild_background_noise_tolerated(tmp_path):
    sheet = draw_sheet(tmp_path / "sheet.png", noise=12)
    info = crop.crop_sheet(sheet, tmp_path / "views")
    assert len(info["views"]) == 4


def test_neighbour_pixels_painted_with_background(tmp_path):
    # a stray mark close to figure 2 but beyond the gap midpoint must not appear in view 1
    sheet = draw_sheet(tmp_path / "sheet.png")
    info = crop.crop_sheet(sheet, tmp_path / "views", pad_frac=0.9)  # huge padding reaches neighbours
    a = np.asarray(Image.open(info["views"]["front"]["path"]).convert("RGB"), dtype=int)
    fg = np.abs(a - np.array(info["background_rgb"])).max(axis=2) > 24
    labels = np.where(fg.any(axis=0))[0]
    # exactly one contiguous figure
    assert (np.diff(labels) == 1).all()


def test_small_views_warn(tmp_path):
    sheet = draw_sheet(tmp_path / "sheet.png", size=(1600, 700))
    info = crop.crop_sheet(sheet, tmp_path / "views")
    assert "warning" in info  # views are far below 900 px wide
