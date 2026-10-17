import numpy as np
import pytest

from dental_image_organizer.core.color import (
    CHANNEL_LIMITS,
    LabCurves,
    apply_lab_curves,
    fit_lab_curves,
    from_lab,
    to_lab,
)
from dental_image_organizer.core.masks import tooth_mask
from support.synthetic import make_scene


def _delta_e(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return np.linalg.norm(to_lab(a) - to_lab(b), axis=-1)


def _shifted(reference: np.ndarray) -> np.ndarray:
    """A different exposure, tone curve, and white balance, like another session's photo."""
    lab = to_lab(reference)
    lab[..., 0] = 100.0 * (lab[..., 0] / 100.0) ** 1.15 - 3.0
    lab[..., 1] += 5.0
    lab[..., 2] -= 6.0
    return from_lab(lab)


@pytest.fixture(scope="module")
def reference() -> np.ndarray:
    return make_scene(31, 1200, 800)


def test_identical_images_need_no_correction(reference: np.ndarray) -> None:
    valid = np.ones(reference.shape[:2], bool)

    curves = fit_lab_curves(reference, reference, valid, tooth_mask(reference))

    assert _delta_e(apply_lab_curves(reference, curves), reference).mean() < 0.5


def test_known_tone_and_white_balance_shift_is_undone(reference: np.ndarray) -> None:
    photo = _shifted(reference)
    valid = np.ones(reference.shape[:2], bool)
    assert _delta_e(photo, reference).mean() > 6  # the shift is clearly visible before

    curves = fit_lab_curves(photo, reference, valid, tooth_mask(reference))

    assert _delta_e(apply_lab_curves(photo, curves), reference).mean() < 3


@pytest.mark.parametrize("share", [0.2, 0.4])
def test_surgical_change_does_not_drag_the_fit(reference: np.ndarray, share: float) -> None:
    photo = _shifted(reference)
    h, w = reference.shape[:2]
    patch = np.zeros((h, w), bool)
    patch[:, : int(w * share)] = True
    rng = np.random.default_rng(5)
    blood = np.array([120, 20, 30]) + rng.integers(-15, 16, (h, w, 3))
    photo[patch] = np.clip(blood[patch], 0, 255).astype(np.uint8)
    valid = np.ones((h, w), bool)

    curves = fit_lab_curves(photo, reference, valid, tooth_mask(reference))

    corrected = apply_lab_curves(photo, curves)
    assert _delta_e(corrected, reference)[~patch].mean() < 3


def test_only_valid_pixels_are_used(reference: np.ndarray) -> None:
    photo = _shifted(reference)
    photo[:, :400] = 0  # outside the photo's coverage after warping
    valid = np.ones(reference.shape[:2], bool)
    valid[:, :400] = False

    curves = fit_lab_curves(photo, reference, valid, tooth_mask(reference))

    corrected = apply_lab_curves(photo, curves)
    assert _delta_e(corrected, reference)[valid].mean() < 3


def test_curve_moves_are_limited_and_monotone(reference: np.ndarray) -> None:
    lab = to_lab(reference)
    lab[..., 0] = np.clip(lab[..., 0] - 50.0, 0, 100)  # far darker than any real exposure error
    photo = from_lab(lab)
    valid = np.ones(reference.shape[:2], bool)

    curves = fit_lab_curves(photo, reference, valid, tooth_mask(reference))

    for curve, limit in zip(curves.curves, CHANNEL_LIMITS, strict=True):
        src, dst = np.array(curve.src), np.array(curve.dst)
        assert np.all(np.abs(dst - src) <= limit + 1e-6)
        assert np.all(np.diff(dst) >= 0)
        assert np.all(np.diff(src) > 0)


def test_params_round_trip(reference: np.ndarray) -> None:
    valid = np.ones(reference.shape[:2], bool)
    curves = fit_lab_curves(_shifted(reference), reference, valid, None)

    assert LabCurves.from_params(curves.to_params()) == curves


def test_chunked_apply_matches_single_pass(reference: np.ndarray) -> None:
    valid = np.ones(reference.shape[:2], bool)
    curves = fit_lab_curves(_shifted(reference), reference, valid, None)

    chunked = apply_lab_curves(reference, curves, chunk_rows=37)
    single = apply_lab_curves(reference, curves, chunk_rows=10_000)

    np.testing.assert_array_equal(chunked, single)
