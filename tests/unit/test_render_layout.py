import numpy as np
import pytest

from dental_image_organizer.core import transform as tf
from dental_image_organizer.core.layout import render_sequence
from dental_image_organizer.core.render import coverage_mask, render
from support.synthetic import make_scene


def test_identity_render_at_another_resolution_is_a_resize() -> None:
    scene = make_scene(41, 800, 600)

    half = render(scene, tf.identity(), (400, 300))

    assert half.shape == (300, 400, 3)
    assert (
        np.abs(half.astype(int).mean(axis=(0, 1)) - scene.astype(int).mean(axis=(0, 1))).max() < 3
    )


def test_render_moves_content_where_the_transform_says() -> None:
    image = np.zeros((100, 200, 3), np.uint8)
    image[40:60, 90:110] = 255  # square centered at (100, 50)
    shift = tf.to_normalized(tf.translation(30, 10), 200, 200)

    out = render(image, shift, (200, 100))

    ys, xs = np.nonzero(out[..., 0] > 128)
    assert xs.mean() == pytest.approx(130, abs=1)
    assert ys.mean() == pytest.approx(60, abs=1)


def test_strong_downscale_is_antialiased() -> None:
    stripes = np.zeros((1000, 1000, 3), np.uint8)
    stripes[:, ::2] = 255  # finest possible detail

    small = render(stripes, tf.identity(), (100, 100))

    # Proper averaging gives uniform gray; naive point sampling gives black or white.
    assert np.abs(small.astype(int) - 128).max() < 20


def test_two_times_downscale_is_antialiased() -> None:
    stripes = np.zeros((1000, 1000, 3), np.uint8)
    stripes[:, ::2] = 255

    half = render(stripes, tf.identity(), (500, 500))

    # Exactly 2x is where an unfiltered warp degenerates to point sampling.
    assert np.abs(half.astype(int) - 128).max() < 20


def test_coverage_mask_marks_only_covered_pixels() -> None:
    shift = tf.to_normalized(tf.translation(50, 0), 100, 100)

    mask = coverage_mask((100, 100), shift, (100, 100))

    assert not mask[:, :50].any()
    assert mask[:, 51:].all()


def test_sequence_places_images_left_to_right_at_equal_height() -> None:
    red = np.full((100, 150, 3), (255, 0, 0), np.uint8)
    green = np.full((100, 120, 3), (0, 255, 0), np.uint8)

    strip = render_sequence([red, green], gap_px=4, gap_color=(255, 255, 255))

    assert strip.shape == (100, 274, 3)
    assert tuple(strip[50, 10]) == (255, 0, 0)
    assert tuple(strip[50, 152]) == (255, 255, 255)
    assert tuple(strip[50, 200]) == (0, 255, 0)


def test_sequence_scales_taller_images_down_to_the_shortest() -> None:
    small = np.zeros((100, 150, 3), np.uint8)
    tall = np.zeros((200, 300, 3), np.uint8)

    strip = render_sequence([small, tall])

    assert strip.shape == (100, 300, 3)


def test_empty_sequence_is_rejected() -> None:
    with pytest.raises(ValueError):
        render_sequence([])
