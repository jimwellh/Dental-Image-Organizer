"""Collage layouts: the horizontal procedure sequence (spec §3.2 step 6)."""

from __future__ import annotations

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
) -> RGBImage:
    """Place images left to right at equal height, in the given order."""
    if not images:
        raise ValueError("A sequence needs at least one image.")
    height = min(img.shape[0] for img in images)
    # Photos of one case share the reference frame, so resizing only happens for mixed input.
    resized = [
        img
        if img.shape[0] == height
        else cast(
            RGBImage,
            cv2.resize(
                img,
                (max(1, round(img.shape[1] * height / img.shape[0])), height),
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
