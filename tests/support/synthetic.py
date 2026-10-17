"""Deterministic dental-like test scenes (spec §8.1). No patient data is ever needed."""

from __future__ import annotations

import cv2
import numpy as np

from dental_image_organizer.core import transform as tf

GUM_RGB = np.array([190, 95, 105], np.float32)
ENAMEL_RGB = np.array([232, 218, 186], np.float32)


def _smooth_noise(rng: np.random.Generator, height: int, width: int, cell: int) -> np.ndarray:
    small = rng.random((max(2, height // cell), max(2, width // cell))).astype(np.float32)
    return cv2.resize(small, (width, height), interpolation=cv2.INTER_CUBIC)


def make_scene(
    seed: int,
    width: int = 2400,
    height: int = 1600,
    *,
    teeth: bool = True,
    texture: bool = True,
    specks: int = 600,
) -> np.ndarray:
    """Pink gum with two rows of ivory teeth, multi-scale texture, and dark specks.

    The texture is a random field, so every neighborhood is unique and SIFT matches are
    unambiguous; the specks add sharp corners.
    """
    rng = np.random.default_rng(seed)
    field = np.zeros((height, width), np.float32)
    if texture:
        for cell, amplitude in ((200, 0.5), (40, 0.3), (8, 0.2)):
            field += amplitude * _smooth_noise(rng, height, width, cell)
        field = (field - field.mean()) / (field.std() + 1e-6)
    img = GUM_RGB + field[..., None] * np.array([18, 12, 12], np.float32)
    if teeth:
        layer = np.zeros((height, width), np.uint8)
        for row_y in (0.40, 0.62):
            for i in range(9):
                center = (
                    int(width * (0.08 + 0.84 * i / 8) + rng.integers(-15, 16)),
                    int(height * row_y + rng.integers(-20, 21)),
                )
                axes = (
                    int(width * 0.038 + rng.integers(-10, 11)),
                    int(height * 0.09 + rng.integers(-10, 11)),
                )
                cv2.ellipse(layer, center, axes, float(rng.uniform(-10, 10)), 0, 360, 255, -1)
        enamel = ENAMEL_RGB + field[..., None] * np.array([10, 10, 12], np.float32)
        img[layer > 0] = enamel[layer > 0]
    for _ in range(specks):
        center = (int(rng.integers(0, width)), int(rng.integers(0, height)))
        color = tuple(float(c) for c in rng.integers(40, 120, 3))
        cv2.circle(img, center, int(rng.integers(2, 7)), color, -1)
    return np.clip(img, 0, 255).astype(np.uint8)


def warp(scene: np.ndarray, h_px: tf.Matrix, size: tuple[int, int]) -> np.ndarray:
    return cv2.warpPerspective(
        scene, h_px, size, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE
    )


def tilt_homography(
    width: int, height: int, degrees: float, focal: float | None = None
) -> tf.Matrix:
    """Homography of a camera tilted about the horizontal axis through the image center."""
    f = focal if focal is not None else float(width)
    k = np.array([[f, 0, width / 2], [0, f, height / 2], [0, 0, 1]])
    a = np.radians(degrees)
    r = np.array([[1, 0, 0], [0, np.cos(a), -np.sin(a)], [0, np.sin(a), np.cos(a)]])
    return tf.normalize(k @ r @ np.linalg.inv(k))


def grade(rgb: np.ndarray, *, gain: float = 1.08, warm: float = 6.0) -> np.ndarray:
    """Imitate a dentist's edit: slightly brighter and warmer."""
    out = rgb.astype(np.float32) * gain + np.array([warm, 0, -warm], np.float32)
    return np.clip(out, 0, 255).astype(np.uint8)
