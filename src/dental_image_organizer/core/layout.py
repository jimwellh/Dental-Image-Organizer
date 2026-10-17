"""Collage layouts: the horizontal procedure sequence (spec §3.2 step 6)."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import cast

import cv2
import numpy as np

from dental_image_organizer.core.imageio import MAX_PIXELS, RGBImage

# The JPEG width limit alone left the height unbounded: nine 2x-scaled 12 MP frames reached
# ~357 MP. The cap is the decoder's own limit (200 MP, 600 MB as RGB uint8) so the app can read
# back every sequence it writes; the earlier 1 GB cap (333 MP) produced strips decode() rejects.
MAX_SEQUENCE_PIXELS = MAX_PIXELS


def render_sequence(
    images: Sequence[RGBImage],
    *,
    gap_px: int = 0,
    gap_color: tuple[int, int, int] = (0, 0, 0),
    max_width: int | None = None,
    max_pixels: int | None = None,
) -> RGBImage:
    """Place images left to right at equal height, in the given order."""
    if not images:
        raise ValueError("A sequence needs at least one image.")
    height = fit_strip_height(
        [_size(img) for img in images],
        gap_px=gap_px,
        max_width=max_width,
        max_pixels=max_pixels,
    )
    resized = [scale_to_height(img, height) for img in images]
    width = sum(img.shape[1] for img in resized) + gap_px * (len(resized) - 1)
    canvas = np.empty((height, width, 3), dtype=np.uint8)
    canvas[:] = gap_color
    x = 0
    for img in resized:
        canvas[:, x : x + img.shape[1]] = img
        x += img.shape[1] + gap_px
    return canvas


def scale_to_height(image: RGBImage, height: int) -> RGBImage:
    """Resize to `height` keeping the aspect ratio; returns `image` itself if it already fits."""
    # Photos of one case share the reference frame, so most frames need no copy at all.
    if image.shape[0] == height:
        return image
    return cast(
        RGBImage,
        cv2.resize(
            image,
            (_scaled_width(_size(image), height), height),
            interpolation=cv2.INTER_AREA,
        ),
    )


def fit_strip_height(
    frame_sizes: Sequence[tuple[int, int]],
    *,
    gap_px: int = 0,
    max_width: int | None = None,
    max_pixels: int | None = None,
) -> int:
    """Strip height for frames given as (width, height): never above the shortest frame.

    The strip stays within `max_width` and `max_pixels` when it can; a `None` limit is ignored.
    """
    if not frame_sizes:
        raise ValueError("A sequence needs at least one frame.")
    height = min(frame_height for _, frame_height in frame_sizes)
    gaps = gap_px * (len(frame_sizes) - 1)
    # The strip is a summary image, so it shrinks to fit its limits instead of failing after
    # every single photo has already been written.
    if max_width is not None:
        natural = _strip_width(frame_sizes, height, gap_px)
        if natural > max_width:
            height = min(height, math.floor(height * (max_width - gaps) / (natural - gaps)))
    if max_pixels is not None:
        height = min(height, _pixel_bound(frame_sizes, gaps, max_pixels))
    height = max(1, height)
    # Per-frame rounding can leave the estimates a few pixels over a limit.
    while height > 1 and _exceeds(frame_sizes, height, gap_px, max_width, max_pixels):
        height -= 1
    return height


def _size(image: RGBImage) -> tuple[int, int]:
    return int(image.shape[1]), int(image.shape[0])


def _scaled_width(size: tuple[int, int], height: int) -> int:
    width, frame_height = size
    return max(1, round(width * height / frame_height))


def _strip_width(frame_sizes: Sequence[tuple[int, int]], height: int, gap_px: int) -> int:
    widths = [_scaled_width(size, height) for size in frame_sizes]
    return sum(widths) + gap_px * (len(widths) - 1)


def _pixel_bound(frame_sizes: Sequence[tuple[int, int]], gaps: int, max_pixels: int) -> int:
    # Width grows linearly with height (k * h + gaps), so area <= max_pixels is a quadratic in h
    # and has a closed-form root; no search over millions of candidate heights is needed.
    k = sum(width / height for width, height in frame_sizes)
    return math.floor((-gaps + math.sqrt(gaps * gaps + 4 * k * max_pixels)) / (2 * k))


def _exceeds(
    frame_sizes: Sequence[tuple[int, int]],
    height: int,
    gap_px: int,
    max_width: int | None,
    max_pixels: int | None,
) -> bool:
    width = _strip_width(frame_sizes, height, gap_px)
    return (max_width is not None and width > max_width) or (
        max_pixels is not None and width * height > max_pixels
    )
