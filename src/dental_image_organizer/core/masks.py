"""Tooth and gingiva masks from CIE Lab thresholds (spec §4.7.1)."""

from __future__ import annotations

import cv2
import numpy as np
import numpy.typing as npt

from dental_image_organizer.core.color import to_lab
from dental_image_organizer.core.imageio import RGBImage

BoolMask = npt.NDArray[np.bool_]

TOOTH_L_PERCENTILE = 55.0
TOOTH_MAX_A = 14.0
TOOTH_B_RANGE = (4.0, 45.0)
GINGIVA_MIN_A = 15.0
GINGIVA_MIN_L = 25.0
KERNEL_DIVISOR = 110
MIN_COMPONENT_SHARE = 0.002


def tooth_mask(rgb: RGBImage) -> BoolMask:
    lab = to_lab(rgb)
    lightness, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
    threshold = float(np.percentile(lightness, TOOTH_L_PERCENTILE))
    raw = (
        (lightness > threshold)
        & (a < TOOTH_MAX_A)
        & (b > TOOTH_B_RANGE[0])
        & (b < TOOTH_B_RANGE[1])
    )
    return _clean(raw)


def gingiva_mask(rgb: RGBImage, teeth: BoolMask | None = None) -> BoolMask:
    lab = to_lab(rgb)
    raw = (lab[..., 1] > GINGIVA_MIN_A) & (lab[..., 0] > GINGIVA_MIN_L)
    if teeth is None:
        teeth = tooth_mask(rgb)
    return _clean(raw & ~teeth)


def _clean(mask: BoolMask) -> BoolMask:
    h, w = mask.shape
    k = max(3, round(min(h, w) / KERNEL_DIVISOR)) | 1
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    opened = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_OPEN, kernel)
    closed = cv2.morphologyEx(opened, cv2.MORPH_CLOSE, kernel)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(closed, connectivity=8)
    keep = np.zeros(count, dtype=bool)
    keep[1:] = stats[1:, cv2.CC_STAT_AREA] >= MIN_COMPONENT_SHARE * h * w
    return np.asarray(keep[labels], dtype=np.bool_)
