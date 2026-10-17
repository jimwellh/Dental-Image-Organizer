"""Photo/reference pairs with known ground truth, built from synthetic scenes."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from dental_image_organizer.core import transform as tf
from support.synthetic import grade, warp

REFERENCE_BOX = (600, 400, 1200, 800)  # x, y, w, h of the scene region the reference shows


@dataclass(frozen=True)
class Pair:
    photo: np.ndarray
    reference: np.ndarray
    truth: tf.Matrix  # photo px -> reference px


def about_center(
    width: int, height: int, scale: float, angle: float, *, mirror: bool = False
) -> tf.Matrix:
    return (
        tf.translation(width / 2, height / 2)
        @ tf.similarity(scale, angle, 0.0, 0.0, mirror=mirror)
        @ tf.translation(-width / 2, -height / 2)
    )


def make_pair(
    scene: np.ndarray,
    scene_to_photo: tf.Matrix,
    *,
    box: tuple[int, int, int, int] = REFERENCE_BOX,
    reference_scale: float = 1.0,
) -> Pair:
    x, y, w, h = box
    crop = scene[y : y + h, x : x + w]
    if reference_scale != 1.0:
        size = (round(w * reference_scale), round(h * reference_scale))
        crop = cv2.resize(crop, size, interpolation=cv2.INTER_AREA)
    scene_to_reference = tf.scaling(reference_scale) @ tf.translation(-x, -y)
    photo = warp(scene, scene_to_photo, (scene.shape[1], scene.shape[0]))
    return Pair(
        photo=photo,
        reference=grade(crop),
        truth=scene_to_reference @ tf.invert(scene_to_photo),
    )


def corner_error(pair: Pair, recovered_px: tf.Matrix) -> float:
    """Largest corner displacement in the reference frame, as a fraction of its width."""
    h, w = pair.reference.shape[:2]
    corners = np.array([[0.0, 0.0], [w, 0.0], [w, h], [0.0, h]])
    photo_points = tf.map_points(tf.invert(pair.truth), corners)
    return float(
        np.linalg.norm(tf.map_points(recovered_px, photo_points) - corners, axis=1).max() / w
    )
