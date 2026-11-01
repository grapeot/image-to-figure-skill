import pytest

from image_to_figure import costs
from image_to_figure.errors import BudgetError
from image_to_figure.workdir import Ledger, next_edit, next_version


def test_image_estimate_scales_with_pixels_and_quality():
    small = costs.estimate_image("1024x1024", "high", 0)
    big = costs.estimate_image("2048x2048", "high", 0)
    assert big == pytest.approx(small * 2, rel=0.05)
    assert costs.estimate_image("1024x1024", "low", 0) < small < costs.estimate_image("1024x1024", "max", 0)
    assert costs.estimate_image("1024x1024", "high", 3) > small


@pytest.mark.parametrize("size,out_tokens,in_images", [
    ("1024x1536", 1372, 0), ("2048x1152", 1413, 1), ("3840x2160", 3336, 2),  # measured 2026-10-31, high quality
])
def test_estimate_stays_above_measured_usage(size, out_tokens, in_images):
    measured = costs.cost_from_usage({"output_tokens": out_tokens,
                                      "input_tokens_details": {"image_tokens": 1536 * in_images, "text_tokens": 400}})
    est = costs.estimate_image(size, "high", in_images)
    assert measured < est < measured * 3


@pytest.mark.parametrize("bad", ["2048", "2048x", "big", "20x20"])
def test_parse_size_rejects(bad):
    with pytest.raises(ValueError):
        costs.parse_size(bad)


def test_cost_from_usage():
    usage = {"input_tokens": 1200, "output_tokens": 1_000_000,
             "input_tokens_details": {"image_tokens": 0, "text_tokens": 0}}
    assert costs.cost_from_usage(usage) == pytest.approx(costs.IMAGE_RATE_OUTPUT)
    assert costs.cost_from_usage(None) is None
    assert costs.cost_from_usage({"input_tokens": 5}) is None


def test_model_costs_cover_every_provider():
    from image_to_figure.fal3d import PROVIDERS

    assert set(costs.MODEL_COST_USD) == set(PROVIDERS)
    with pytest.raises(ValueError):
        costs.estimate_model("nope")


def test_ledger_budget(tmp_path):
    led = Ledger(tmp_path / "spend.json")
    assert led.spent() == 0
    led.check(1.0, None)  # no budget: never refuses
    led.record({"stage": "x", "estimate_usd": 0.5, "actual_usd": None})
    led.record({"stage": "y", "estimate_usd": 0.5, "actual_usd": 0.2})
    assert led.spent() == pytest.approx(0.7)
    info = led.check(0.3, 1.0)
    assert info["remaining_usd"] == pytest.approx(0.3)
    with pytest.raises(BudgetError) as e:
        led.check(0.31, 1.0)
    assert e.value.exit_code == 3
    assert e.value.detail["spent_usd"] == pytest.approx(0.7)


def test_versions(tmp_path):
    assert next_version(tmp_path, "sheet").name == "sheet_v1.png"
    (tmp_path / "sheet_v1.png").write_bytes(b"")
    (tmp_path / "sheet_v3.png").write_bytes(b"")
    assert next_version(tmp_path, "sheet").name == "sheet_v4.png"
    src = tmp_path / "sheet_v1.png"
    assert next_edit(src).name == "sheet_v1_e1.png"
    (tmp_path / "sheet_v1_e1.png").write_bytes(b"")
    assert next_edit(src).name == "sheet_v1_e2.png"
    assert next_edit(tmp_path / "sheet_v1_e1.png").name == "sheet_v1_e2.png"
