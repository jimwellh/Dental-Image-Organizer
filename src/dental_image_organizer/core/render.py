"""Apply an edit record (transform + color) to an original image (spec §4.8 "Rendering")."""

from __future__ import annotations

from typing import cast

import cv2
import numpy as np

from dental_image_organizer.core import transform as tf
from dental_image_organizer.core.color import LabCurves, apply_lab_curves
from dental_image_organizer.core.imageio import RGBImage
from dental_image_organizer.core.masks import BoolMask

# warpPerspective samples without a low-pass filter, so a shrinking warp aliases fine texture;
# at exactly 2x it degenerates to point sampling. Inputs shrunk by more than 1.25x are first
# area-averaged to about the output scale, leaving the warp to rotate and shift at roughly 1:1.
# Milder shrinks keep a single Lanczos resample, which preserves sharpness.
MAX_WARP_DOWNSCALE = 0.8


def render(
    image: RGBImage,
    transform: tf.Matrix,
    output_size: tuple[int, int],
    *,
    curves: LabCurves | None = None,
    interpolation: int = cv2.INTER_LANCZOS4,
) -> RGBImage:
    """Warp `image` (any resolution) with the normalized `transform`, then apply colors."""
    out_w, out_h = output_size
    h_px = tf.to_pixel(transform, image.shape[1], out_w)
    scale = float(np.sqrt(abs(np.linalg.det(h_px[:2, :2]))))
    if scale < MAX_WARP_DOWNSCALE:
        size = (max(1, round(image.shape[1] * scale)), max(1, round(image.shape[0] * scale)))
        image = cast(RGBImage, cv2.resize(image, size, interpolation=cv2.INTER_AREA))
        h_px = tf.to_pixel(transform, image.shape[1], out_w)
    warped = cast(
        RGBImage,
        cv2.warpPerspective(
            image,
            h_px,
            (out_w, out_h),
            flags=interpolation,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=(0, 0, 0),
        ),
    )
    return apply_lab_curves(warped, curves) if curves is not None else warped


def coverage_mask(
    input_size: tuple[int, int], transform: tf.Matrix, output_size: tuple[int, int]
) -> BoolMask:
    """Output pixels that the warped input actually covers."""
    in_w, in_h = input_size
    h_px = tf.to_pixel(transform, in_w, output_size[0])
    covered = cv2.warpPerspective(
        np.full((in_h, in_w), 255, np.uint8), h_px, output_size, flags=cv2.INTER_NEAREST
    )
    return covered > 0
