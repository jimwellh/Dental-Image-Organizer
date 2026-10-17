import numpy as np
import pytest

from dental_image_organizer.core.color import from_lab, to_lab
from support.synthetic import make_scene


def test_lab_uses_natural_units() -> None:
    lab = to_lab(np.array([[[255, 255, 255], [0, 0, 0]]], np.uint8))

    assert lab[0, 0, 0] == pytest.approx(100.0, abs=0.01)
    assert lab[0, 1, 0] == pytest.approx(0.0, abs=0.01)
    assert np.abs(lab[0, :, 1:]).max() < 0.01


def test_lab_round_trip_is_within_one_level() -> None:
    rgb = make_scene(4, 300, 200)

    back = from_lab(to_lab(rgb))

    assert np.abs(back.astype(int) - rgb.astype(int)).max() <= 1
