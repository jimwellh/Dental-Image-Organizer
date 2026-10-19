"""Apply an edit record (transform + color) to an original image (spec §4.8 "Rendering")."""

from __future__ import annotations

from typing import cast

import cv2
import numpy as np

from dental_image_organizer.core import transform as tf
from dental_image_organizer.core.color import LabTransform, apply_lab_transform
from dental_image_organizer.core.imageio import RGBImage
from dental_image_organizer.core.masks import BoolMask

# warpPerspective samples without a low-pass filter, so a shrinking warp aliases fine texture;
# at exactly 2x it degenerates to point sampling. Inputs shrunk by more than 1.25x are first
# area-averaged to about the output scale, leaving the warp to rotate and shift at roughly 1:1.
# Milder shrinks deliberately keep a single Lanczos resample for sharpness; they can fold detail
# near Nyquist, where iPhone optics leave little energy.
MAX_WARP_DOWNSCALE = 0.8
# cv2.remap, which warpPerspective runs for Lanczos, asserts that each side of the source is
# below SHRT_MAX (32767); a larger source is shrunk to fit before the warp.
MAX_WARP_SOURCE_SIDE = 32766


def render(
    image: RGBImage,
    transform: tf.Matrix,
    output_size: tuple[int, int],
    *,
    color: LabTransform | None = None,
    interpolation: int = cv2.INTER_LANCZOS4,
    border_mode: int = cv2.BORDER_CONSTANT,
) -> RGBImage:
    """Warp `image` (any resolution) with the normalized `transform`, then apply colors."""
    out_w, out_h = output_size
    h_px = tf.to_pixel(transform, image.shape[1], out_w)
    scale = float(np.sqrt(abs(np.linalg.det(h_px[:2, :2]))))
    fit = MAX_WARP_SOURCE_SIDE / max(image.shape[:2])
    if scale < MAX_WARP_DOWNSCALE or fit < 1.0:
        shrink = min(scale, fit)
        size = (max(1, round(image.shape[1] * shrink)), max(1, round(image.shape[0] * shrink)))
        image = cast(RGBImage, cv2.resize(image, size, interpolation=cv2.INTER_AREA))
        h_px = tf.to_pixel(transform, image.shape[1], out_w)
    warped = cast(
        RGBImage,
        cv2.warpPerspective(
            image,
            h_px,
            (out_w, out_h),
            flags=interpolation,
            borderMode=border_mode,
            borderValue=(0, 0, 0),
        ),
    )
    if color is None:
        return warped
    graded = apply_lab_transform(warped, color)
    if border_mode == cv2.BORDER_CONSTANT:
        # Color maps keep offsets (curves past their fitted range, affine maps everywhere), so
        # they would tint the black fill outside the photo; that fill is not photo content.
        covered = coverage_mask((image.shape[1], image.shape[0]), transform, output_size)
        graded[~covered] = 0
    return graded


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
