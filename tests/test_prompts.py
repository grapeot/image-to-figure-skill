import pytest

from image_to_figure import prompts


def test_lookdev_forbids_invented_details_and_keeps_description():
    p = prompts.lookdev_prompt("red scarf on the left shoulder", heads=2.5)
    assert "2.5 heads tall" in p
    assert "red scarf on the left shoulder" in p
    assert "Do not add" in p


def test_turnaround_has_four_views_in_order_and_ground_line():
    p = prompts.turnaround_prompt(None, n_refs=2)
    for words in ("1) front view", "2) left side view", "3) back view", "4) right side view"):
        assert words in p
    assert "same ground line" in p
    assert "Image(s) 2, 3" in p
    assert "{" not in p


def test_edit_numbers_fixes_and_requires_one():
    p = prompts.edit_prompt(["remove the tail", "plain pockets"], n_refs=1)
    assert "1. remove the tail" in p and "2. plain pockets" in p
    assert "pixel-for-pixel identical" in p
    assert "image(s) 2" in p
    with pytest.raises(ValueError):
        prompts.edit_prompt([])
