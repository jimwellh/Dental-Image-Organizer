import cv2
import numpy as np
import pytest

from dental_image_organizer.core import transform as tf
from dental_image_organizer.core.geometry import (
    ANALYSIS_LONG_EDGE,
    MAX_ZOOM,
    estimate_level,
    flip_matrix,
    flip_pixels,
    normalize_view,
)
from dental_image_organizer.core.imageio import resize_long_edge
from dental_image_organizer.core.masks import tooth_mask
from dental_image_organizer.core.render import coverage_mask, render
from dental_image_organizer.core.slots import DEFAULT_FLIPS, FLIPS, SLOTS, Flip, Slot
from support.synthetic import ENAMEL_RGB, GUM_RGB, _smooth_noise
from support.views import VIEW_SIZE, make_view

CELL = (1200, 800)


@pytest.mark.parametrize("slot", SLOTS)
@pytest.mark.parametrize("tilt", [-12.0, 7.0])
def test_level_recovers_known_tilt_within_one_degree(slot: Slot, tilt: float) -> None:  # §8.1
    level = estimate_level(make_view(slot, 3, angle=tilt), slot)

    assert level.confident
    assert level.angle_deg == pytest.approx(-tilt, abs=1.0)


def test_occlusion_falls_back_to_the_darkest_row_when_arches_touch() -> None:
    # Blurring closes the thin gap between the arches in the tooth mask.
    blurred = cv2.GaussianBlur(make_view("occlusion_front", 4, angle=6.0), (0, 0), 4)

    assert estimate_level(blurred, "occlusion_front").angle_deg == pytest.approx(-6.0, abs=1.0)


def test_occlusion_reads_the_darkest_row_when_no_column_has_an_opening() -> None:
    scene = _touching_arches(9, angle=-8.0)
    small, _ = resize_long_edge(scene, ANALYSIS_LONG_EDGE)
    # Guard: with no opening anywhere, every boundary point comes from the darkest row.
    columns = (np.flatnonzero(column) for column in tooth_mask(small).T)
    assert not any(np.any(np.diff(rows) > 1) for rows in columns)

    level = estimate_level(scene, "occlusion_front")

    assert level.confident
    assert level.angle_deg == pytest.approx(8.0, abs=1.0)


def _touching_arches(seed: int, *, angle: float) -> np.ndarray:
    """Both arches as one textured tooth block; the shadow where they meet is still enamel.

    The texture puts each column's brightest row at random, so only the darkest row lines up.
    """
    w, h = VIEW_SIZE
    rng = np.random.default_rng(seed)
    field = sum(a * _smooth_noise(rng, h, w, c) for c, a in ((200, 0.5), (40, 0.3), (8, 0.2)))
    field = (field - field.mean()) / (field.std() + 1e-6)
    img = GUM_RGB + field[..., None] * np.array([16, 10, 10], np.float32)
    enamel = ENAMEL_RGB + field[..., None] * np.array([9, 9, 11], np.float32)
    top, bottom, left, right = int(0.27 * h), int(0.73 * h), int(0.1 * w), int(0.9 * w)
    img[top:bottom, left:right] = enamel[top:bottom, left:right]
    img[h // 2 - 12 : h // 2 + 12, left:right] *= 0.8
    upright = np.clip(img, 0, 255).astype(np.uint8)
    m = cv2.getRotationMatrix2D((w / 2, h / 2), -angle, 1.0)
    return cv2.warpAffine(
        upright, m, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE
    )


def test_view_without_teeth_is_left_unrotated() -> None:
    gum_only = np.full((800, 1200, 3), (190, 95, 105), np.uint8)

    level = estimate_level(gum_only, "occlusion_front")

    assert (level.angle_deg, level.confident) == (0.0, False)


@pytest.mark.parametrize("flip", FLIPS)
def test_flip_matrix_matches_flip_pixels(flip: Flip) -> None:
    image = np.zeros((40, 60, 3), np.uint8)
    image[5, 10] = 255

    moved = render(image, tf.to_normalized(flip_matrix(flip, 60, 40), 60, 60), (60, 40))

    np.testing.assert_array_equal(moved > 0, flip_pixels(image, flip) > 0)


@pytest.mark.parametrize("slot", SLOTS)
def test_cell_is_level_cropped_and_free_of_black_corners(slot: Slot) -> None:
    photo = make_view(slot, 5, angle=9.0)

    geometry = normalize_view(photo, slot, "none", CELL)
    cell = render(photo, geometry.transform, CELL)

    assert coverage_mask((photo.shape[1], photo.shape[0]), geometry.transform, CELL).all()
    assert abs(estimate_level(cell, slot).angle_deg) <= 0.5
    assert tooth_mask(cell).mean() > 0.1


@pytest.mark.parametrize("slot", SLOTS)
def test_mirror_shot_with_its_default_flip_matches_the_upright_view(slot: Slot) -> None:
    upright = make_view(slot, 6, angle=4.0)
    mirror_shot = flip_pixels(upright, DEFAULT_FLIPS[slot])

    fixed = render(
        mirror_shot, normalize_view(mirror_shot, slot, DEFAULT_FLIPS[slot], CELL).transform, CELL
    )
    expected = render(upright, normalize_view(upright, slot, "none", CELL).transform, CELL)

    assert np.abs(fixed.astype(int) - expected.astype(int)).mean() < 3


def test_zoom_never_exceeds_the_limit_when_few_teeth_show() -> None:
    hidden = make_view("occlusion_front", 7)
    # Hide all but a narrow band of teeth, as retractors and lips sometimes do.
    hidden[:, : VIEW_SIZE[0] * 2 // 5] = (190, 95, 105)
    hidden[:, VIEW_SIZE[0] * 3 // 5 :] = (190, 95, 105)

    geometry = normalize_view(hidden, "occlusion_front", "none", CELL)

    scale = tf.decompose_similarity(tf.to_pixel(geometry.transform, VIEW_SIZE[0], CELL[0])).scale
    assert CELL[0] / scale >= VIEW_SIZE[0] / MAX_ZOOM - 1


def test_portrait_photo_gets_a_landscape_cell() -> None:
    portrait = np.ascontiguousarray(make_view("upper_occlusal", 8).transpose(1, 0, 2))

    geometry = normalize_view(portrait, "upper_occlusal", "none", CELL)

    assert coverage_mask((portrait.shape[1], portrait.shape[0]), geometry.transform, CELL).all()


def test_photo_without_teeth_keeps_the_whole_frame() -> None:  # Review Focus 3
    gum_only = np.full((1200, 1800, 3), (190, 95, 105), np.uint8)

    geometry = normalize_view(gum_only, "occlusion_front", "none", CELL)

    assert not geometry.level.confident
    assert coverage_mask((1800, 1200), geometry.transform, CELL).all()
    scale = tf.decompose_similarity(tf.to_pixel(geometry.transform, 1800, CELL[0])).scale
    assert CELL[0] / scale == pytest.approx(1800, rel=0.01)
