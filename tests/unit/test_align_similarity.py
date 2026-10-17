import numpy as np
import pytest

from dental_image_organizer.core import transform as tf
from dental_image_organizer.core.align import align_to_reference, prepare_reference
from support.scenarios import about_center, corner_error, make_pair
from support.synthetic import grade, make_scene

W, H = 2400, 1600


@pytest.fixture(scope="module")
def scene() -> np.ndarray:
    return make_scene(11, W, H)


@pytest.mark.parametrize(
    ("scale", "angle", "mirror"),
    [
        (1.0, 0.0, False),
        (1.0, 7.0, False),
        (0.7, -12.0, False),
        (1.4, 12.0, False),
        (1.0, 5.0, True),
        (0.85, -6.0, True),
    ],
)
def test_recovers_known_similarity(
    scene: np.ndarray, scale: float, angle: float, mirror: bool
) -> None:
    pair = make_pair(scene, about_center(W, H, scale, angle, mirror=mirror))

    result = align_to_reference(pair.photo, prepare_reference(pair.reference))

    got = tf.to_pixel(result.transform, W, pair.reference.shape[1])
    recovered, truth = tf.decompose_similarity(got), tf.decompose_similarity(pair.truth)
    assert result.status == "ok"
    assert result.confidence >= 0.6
    assert result.mirrored is mirror
    assert abs(recovered.angle_deg - truth.angle_deg) <= 0.5
    assert recovered.scale == pytest.approx(truth.scale, rel=0.02)
    rw, rh = pair.reference.shape[1], pair.reference.shape[0]
    center = [[rw / 2, rh / 2]]
    shift = np.linalg.norm(
        tf.map_points(tf.invert(got), center) - tf.map_points(tf.invert(pair.truth), center)
    )
    assert shift <= 0.01 * W
    assert result.detail_ratio == pytest.approx(1.0 / truth.scale, rel=0.05)


def test_reference_smaller_than_working_size(scene: np.ndarray) -> None:
    pair = make_pair(scene, about_center(W, H, 1.0, 4.0), reference_scale=0.6)

    result = align_to_reference(pair.photo, prepare_reference(pair.reference))

    assert result.status == "ok"
    assert corner_error(pair, tf.to_pixel(result.transform, W, pair.reference.shape[1])) < 0.005


def test_photo_identical_to_reference_maps_to_identity(scene: np.ndarray) -> None:
    reference = grade(scene[400:1200, 600:1800])

    result = align_to_reference(reference, prepare_reference(reference))

    assert result.status == "ok"
    np.testing.assert_allclose(result.transform, np.eye(3), atol=2e-3)


def test_unrelated_photo_needs_review_with_fallback(scene: np.ndarray) -> None:
    reference = grade(scene[400:1200, 600:1800])

    result = align_to_reference(make_scene(99, W, H), prepare_reference(reference))

    assert result.status == "needs_review"
    assert result.estimated is False
    assert result.message


def test_tight_crop_uses_second_pass_and_reports_detail_ratio() -> None:
    big = make_scene(12, 4800, 3200)
    # The reference shows 30% of the photo's width, downscaled to 0.8 by the dentist's export.
    pair = make_pair(
        big, about_center(4800, 3200, 1.0, 5.0), box=(1680, 1100, 1440, 960), reference_scale=0.8
    )

    result = align_to_reference(pair.photo, prepare_reference(pair.reference))

    got = tf.to_pixel(result.transform, 4800, pair.reference.shape[1])
    assert result.tight_crop is True
    assert result.status == "ok"
    assert (
        abs(tf.decompose_similarity(got).angle_deg - tf.decompose_similarity(pair.truth).angle_deg)
        <= 0.5
    )
    assert corner_error(pair, got) < 0.01
    assert result.detail_ratio == pytest.approx(1.25, rel=0.05)
