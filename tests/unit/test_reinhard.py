import numpy as np

from dental_image_organizer.core import transform as tf
from dental_image_organizer.core.color import LabAffine, apply_lab_transform, fit_reinhard, to_lab
from dental_image_organizer.core.masks import gingiva_mask, tooth_mask
from dental_image_organizer.core.render import render
from support.synthetic import grade, make_scene


def _region(rgb: np.ndarray) -> np.ndarray:
    teeth = tooth_mask(rgb)
    return teeth | gingiva_mask(rgb, teeth)


def _tooth_mean(rgb: np.ndarray) -> np.ndarray:
    return to_lab(rgb)[tooth_mask(rgb)].astype(np.float64).mean(axis=0)


def test_identical_cells_need_no_correction() -> None:
    cell = make_scene(1, 1200, 800)

    affine = fit_reinhard(cell, _region(cell), cell, _region(cell))

    np.testing.assert_allclose(affine.gains, 1.0, atol=1e-6)
    np.testing.assert_allclose(affine.offsets, 0.0, atol=1e-4)


def test_tooth_color_difference_drops_by_at_least_60_percent() -> None:  # spec §8.1
    baseline = make_scene(2, 1200, 800)
    other = grade(baseline, gain=0.85, warm=-8.0)

    affine = fit_reinhard(other, _region(other), baseline, _region(baseline))
    fixed = apply_lab_transform(other, affine)

    before = np.linalg.norm(_tooth_mean(other) - _tooth_mean(baseline))
    after = np.linalg.norm(_tooth_mean(fixed) - _tooth_mean(baseline))
    assert after <= 0.4 * before


def test_mean_shift_is_clamped() -> None:
    baseline = make_scene(3, 1200, 800)
    region = _region(baseline)
    far = np.clip(baseline.astype(int) + 90, 0, 255).astype(np.uint8)

    affine = fit_reinhard(baseline, region, far, region)

    # float64 means: a float32 column mean over ~2M pixels drifts by about 0.1.
    before = to_lab(baseline)[region].astype(np.float64)
    moved = affine.apply_lab(before).astype(np.float64).mean(axis=0) - before.mean(axis=0)
    assert abs(moved[0]) <= 0.8 * 12.0 + 1e-3
    assert np.all(np.abs(moved[1:]) <= 0.8 * 8.0 + 1e-3)


def test_affine_params_round_trip() -> None:
    affine = LabAffine((1.1, 0.9, 1.0), (2.0, -3.0, 0.5))

    assert LabAffine.from_params(affine.to_params()) == affine


def test_render_applies_an_affine_but_keeps_the_fill_black() -> None:
    gray = np.full((100, 100, 3), 128, np.uint8)
    shift = tf.to_normalized(tf.translation(50, 0), 100, 100)

    out = render(gray, shift, (100, 100), color=LabAffine((1.0, 1.0, 1.0), (10.0, 0.0, 0.0)))

    assert out[:, :49].max() == 0
    assert out[:, 60:].mean() > 140
