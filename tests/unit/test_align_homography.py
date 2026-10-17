import numpy as np
import pytest
from structlog.testing import capture_logs

from dental_image_organizer.core import transform as tf
from dental_image_organizer.core.align import (
    align_to_reference,
    homography_gate_failures,
    prepare_reference,
)
from support.scenarios import REFERENCE_BOX, about_center, corner_error, make_pair
from support.synthetic import make_scene, tilt_homography

W, H = 2400, 1600


@pytest.fixture(scope="module")
def scene() -> np.ndarray:
    return make_scene(11, W, H)


def test_camera_tilt_is_corrected_with_a_homography(scene: np.ndarray) -> None:
    pair = make_pair(scene, about_center(W, H, 1.0, 3.0) @ tilt_homography(W, H, 8.0))

    result = align_to_reference(pair.photo, prepare_reference(pair.reference))

    assert result.model == "homography"
    assert result.homography_note is None
    assert corner_error(pair, tf.to_pixel(result.transform, W, pair.reference.shape[1])) < 0.005


def test_pure_similarity_keeps_the_similarity(scene: np.ndarray) -> None:
    pair = make_pair(scene, about_center(W, H, 1.1, -4.0))

    result = align_to_reference(pair.photo, prepare_reference(pair.reference))

    assert result.model == "similarity"
    assert result.homography_note is not None
    assert "ecc_gain" in result.homography_note
    np.testing.assert_allclose(result.transform[2], [0.0, 0.0, 1.0], atol=1e-12)


def test_features_in_one_quadrant_skip_the_homography() -> None:
    flat = make_scene(21, W, H, teeth=False, texture=False, specks=0)
    textured = make_scene(22, W, H, teeth=False)
    x, y, w, h = REFERENCE_BOX
    # Only the top-left quarter of the reference region carries any texture.
    flat[y : y + h // 2, x : x + w // 2] = textured[y : y + h // 2, x : x + w // 2]
    pair = make_pair(flat, about_center(W, H, 1.0, 2.0) @ tilt_homography(W, H, 8.0))

    result = align_to_reference(pair.photo, prepare_reference(pair.reference))

    assert result.model == "similarity"
    assert result.homography_note == "poor_coverage"


FRAME = (1200, 800)


def test_gate_rejects_small_ecc_gain() -> None:
    s = tf.identity()

    failures = homography_gate_failures(s, s, FRAME, homography_cc=0.95, similarity_cc=0.94)

    assert failures == ["ecc_gain"]


def test_gate_rejects_large_corner_shift() -> None:
    s = tf.identity()
    h = tf.identity()
    h[2, 0] = 2e-4  # strong keystone: far corners move by well over 8% of the width

    failures = homography_gate_failures(h, s, FRAME, homography_cc=0.99, similarity_cc=0.9)

    assert "corner_shift" in failures


def test_gate_rejects_frame_behind_the_camera() -> None:
    s = tf.identity()
    h = tf.identity()
    # The inverse has h20 = -2e-3, so frame points with x > 500 come from behind the camera.
    h[2, 0] = 2e-3

    failures = homography_gate_failures(h, s, FRAME, homography_cc=0.99, similarity_cc=0.9)

    assert "not_convex" in failures


def test_gate_accepts_mild_perspective_with_clear_gain() -> None:
    s = tf.identity()
    h = tf.identity()
    h[2, 1] = 2e-5

    assert homography_gate_failures(h, s, FRAME, homography_cc=0.99, similarity_cc=0.95) == []


@pytest.mark.usefixtures("debug_logging")
def test_gate_values_are_logged_for_tuning() -> None:
    s = tf.identity()
    h = tf.identity()
    h[2, 1] = 2e-5

    with capture_logs() as logs:
        homography_gate_failures(h, s, FRAME, homography_cc=0.99, similarity_cc=0.95)

    events = [entry for entry in logs if entry["event"] == "homography_gates"]
    assert len(events) == 1
    assert events[0]["ecc_gain"] == pytest.approx(0.04)
    assert events[0]["failed"] == []
    assert events[0]["convex"] is True
