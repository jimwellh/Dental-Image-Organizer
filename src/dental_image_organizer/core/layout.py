"""Collage layouts: the horizontal procedure sequence (spec §3.2 step 6)."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import cast

import cv2
import numpy as np

from dental_image_organizer.core.imageio import RGBImage


def render_sequence(
    images: Sequence[RGBImage],
    *,
    gap_px: int = 0,
    gap_color: tuple[int, int, int] = (0, 0, 0),
    max_width: int | None = None,
) -> RGBImage:
    """Place images left to right at equal height, in the given order."""
    if not images:
        raise ValueError("A sequence needs at least one image.")
    height = min(img.shape[0] for img in images)
    if max_width is not None:
        height = _fit_height(images, height, gap_px, max_width)
    # Photos of one case share the reference frame, so resizing only happens for mixed input.
    resized = [
        img
        if img.shape[0] == height
        else cast(
            RGBImage,
            cv2.resize(
                img,
                (_scaled_width(img, height), height),
                interpolation=cv2.INTER_AREA,
            ),
        )
        for img in images
    ]
    width = sum(img.shape[1] for img in resized) + gap_px * (len(resized) - 1)
    canvas = np.empty((height, width, 3), dtype=np.uint8)
    canvas[:] = gap_color
    x = 0
    for img in resized:
        canvas[:, x : x + img.shape[1]] = img
        x += img.shape[1] + gap_px
    return canvas


def _scaled_width(img: RGBImage, height: int) -> int:
    if img.shape[0] == height:
        return int(img.shape[1])
    scaled: int = round(img.shape[1] * height / img.shape[0])
    return max(1, scaled)


def _strip_width(images: Sequence[RGBImage], height: int, gap_px: int) -> int:
    widths = [_scaled_width(img, height) for img in images]
    return sum(widths) + gap_px * (len(widths) - 1)


def _fit_height(images: Sequence[RGBImage], height: int, gap_px: int, max_width: int) -> int:
    # The strip is a summary image, so it shrinks to fit the encoder's width limit instead of
    # failing after every single photo has already been written.
    natural = _strip_width(images, height, gap_px)
    if natural <= max_width:
        return height
    gaps = gap_px * (len(images) - 1)
    fitted = max(1, math.floor(height * (max_width - gaps) / (natural - gaps)))
    # Per-frame rounding can leave the estimate a few pixels over the limit.
    while fitted > 1 and _strip_width(images, fitted, gap_px) > max_width:
        fitted -= 1
    return fitted
