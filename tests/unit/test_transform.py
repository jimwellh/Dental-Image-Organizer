import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from dental_image_organizer.core import transform as tf

angles = st.floats(min_value=-170, max_value=170)
scales = st.floats(min_value=0.1, max_value=10)
offsets = st.floats(min_value=-1000, max_value=1000)
widths = st.floats(min_value=16, max_value=10000)


@given(scales, angles, offsets, offsets, st.booleans())
def test_decompose_recovers_similarity_parameters(
    scale: float, angle: float, tx: float, ty: float, mirror: bool
) -> None:
    params = tf.decompose_similarity(tf.similarity(scale, angle, tx, ty, mirror=mirror))

    assert params.scale == pytest.approx(scale, rel=1e-9)
    assert params.angle_deg == pytest.approx(angle, abs=1e-7)
    assert (params.tx, params.ty) == pytest.approx((tx, ty), abs=1e-7)
    assert params.mirror is mirror


@given(scales, angles, offsets, offsets, widths, widths)
def test_normalized_and_pixel_round_trip(
    scale: float, angle: float, tx: float, ty: float, in_w: float, out_w: float
) -> None:
    h_px = tf.similarity(scale, angle, tx, ty)

    back = tf.to_pixel(tf.to_normalized(h_px, in_w, out_w), in_w, out_w)

    np.testing.assert_allclose(back, h_px, rtol=1e-9, atol=1e-6)


def test_normalized_matrix_is_resolution_independent() -> None:
    # One stored edit must render the same content at preview and full resolution.
    h_norm = tf.to_normalized(tf.similarity(0.5, 10, 30, 40), in_width=4000, out_width=2000)
    point_full = np.array([[1200.0, 900.0]])

    full = tf.map_points(tf.to_pixel(h_norm, 4000, 2000), point_full)
    preview = tf.map_points(tf.to_pixel(h_norm, 1000, 500), point_full / 4)

    np.testing.assert_allclose(preview, full / 4, atol=1e-9)


@given(scales, angles, offsets, offsets)
def test_invert_composes_to_identity(scale: float, angle: float, tx: float, ty: float) -> None:
    h = tf.similarity(scale, angle, tx, ty)

    np.testing.assert_allclose(tf.invert(h) @ h, np.eye(3), atol=1e-9)


@given(scales, angles, offsets, offsets, st.booleans())
def test_fit_similarity_recovers_exact_transform(
    scale: float, angle: float, tx: float, ty: float, mirror: bool
) -> None:
    truth = tf.similarity(scale, angle, tx, ty, mirror=mirror)
    src = np.array([[0.0, 0.0], [100.0, 0.0], [100.0, 60.0], [0.0, 60.0], [37.0, 12.0]])

    fitted = tf.fit_similarity(src, tf.map_points(truth, src))

    np.testing.assert_allclose(fitted, truth, rtol=1e-7, atol=1e-6)


def test_closest_similarity_of_similarity_is_itself() -> None:
    h = tf.similarity(1.3, -8.0, 12.0, -7.0)

    np.testing.assert_allclose(tf.closest_similarity(h, 1.0, 0.75), h, atol=1e-9)


def test_closest_similarity_drops_perspective() -> None:
    h = tf.similarity(1.0, 3.0, 0.02, 0.01).copy()
    h[2, 0] = 0.05  # mild keystone

    approx = tf.closest_similarity(h, 1.0, 0.75)

    np.testing.assert_allclose(approx[2], [0.0, 0.0, 1.0])
    assert tf.decompose_similarity(approx).angle_deg == pytest.approx(3.0, abs=1.5)
