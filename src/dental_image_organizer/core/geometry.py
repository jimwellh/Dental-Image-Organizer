"""Per-slot geometry of a composite cell: flip, level, and smart crop (spec §4.7.2).

Coordinate frames: "photo" is the input image in its own pixels, at any resolution, and the
"leveled" frame is the photo after its flip and leveling rotation, still in photo pixels.
Masks are measured on an analysis copy whose long edge is ANALYSIS_LONG_EDGE and scaled back.
The returned transform maps the normalized photo onto the normalized cell.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, cast

import cv2
import numpy as np
import numpy.typing as npt
import structlog

from dental_image_organizer.core import transform as tf
from dental_image_organizer.core.color import to_lab
from dental_image_organizer.core.imageio import RGBImage, resize_long_edge
from dental_image_organizer.core.masks import BoolMask, tooth_mask
from dental_image_organizer.core.slots import Flip, Slot, slot_group

log = structlog.get_logger(__name__)

ANALYSIS_LONG_EDGE = 800
MAX_LEVEL_DEG = 15.0
SYMMETRY_STEP_DEG = 0.5
MIN_SYMMETRY_IOU = 0.5
MIN_LINE_COLUMNS = 0.25
MIN_LINE_INLIERS = 0.6
LINE_TOLERANCE = 0.015
RANSAC_TRIALS = 300
CROP_MARGIN = 0.12
CROP_PERCENTILES = (2.0, 98.0)
MAX_ZOOM = 1.3
COLUMN_STEP = 2

# Below this share of the frame a tooth mask is stray highlights, not an arch.
_MIN_TOOTH_SHARE = 0.01
# Columns with fewer tooth pixels than this share of the height only graze an arch.
_MIN_COLUMN_SHARE = 0.05
# The blur mixes gingiva into rows near the ends of a column's tooth span; the arches meet
# well inside it.
_DARK_ROW_SPAN = (0.2, 0.8)
_DARK_ROW_BLUR = (1, 9)
_BISECTION_STEPS = 30

# x, y, width, height in leveled photo pixels.
Rect = tuple[float, float, float, float]
Edge = Literal["top", "bottom"]
FloatPoints = npt.NDArray[np.float64]
Lightness = npt.NDArray[np.float32]

# (mirror x, mirror y) for each flip.
_MIRRORS: dict[Flip, tuple[bool, bool]] = {
    "none": (False, False),
    "horizontal": (True, False),
    "vertical": (False, True),
    "rotate_180": (True, True),
}


@dataclass(frozen=True)
class LevelEstimate:
    """Leveling rotation in the tf.similarity convention; 0 when no estimate is trusted."""

    angle_deg: float
    confident: bool


@dataclass(frozen=True)
class CellGeometry:
    """Normalized photo -> cell transform, with the flip and level it was built from."""

    transform: tf.Matrix
    flip: Flip
    level: LevelEstimate


def flip_pixels(rgb: RGBImage, flip: Flip) -> RGBImage:
    mirror_x, mirror_y = _MIRRORS[flip]
    if not (mirror_x or mirror_y):
        return rgb
    # Contiguous so OpenCV, which rejects negative strides, can read the result directly.
    return np.ascontiguousarray(rgb[:: -1 if mirror_y else 1, :: -1 if mirror_x else 1])


def flip_matrix(flip: Flip, width: int, height: int) -> tf.Matrix:
    """Pixel matrix of `flip`; pixel centers sit on integers, so x' = width - 1 - x."""
    mirror_x, mirror_y = _MIRRORS[flip]
    m = tf.identity()
    if mirror_x:
        m[0, 0], m[0, 2] = -1.0, width - 1.0
    if mirror_y:
        m[1, 1], m[1, 2] = -1.0, height - 1.0
    return m


def estimate_level(rgb: RGBImage, slot: Slot) -> LevelEstimate:
    """Rotation that levels an already flipped view of `slot` (spec §4.7.2 step 2)."""
    small, _ = resize_long_edge(rgb, ANALYSIS_LONG_EDGE)
    teeth = tooth_mask(small)
    h, w = teeth.shape
    group = slot_group(slot)
    angle: float | None
    if group in ("upper_occlusal", "lower_occlusal"):
        angle = _symmetry_angle(teeth)
    elif group in ("occlusion_front", "occlusion_side"):
        angle = _line_angle(_boundary_points(teeth, to_lab(small)[..., 0]), (h, w))
    else:
        edge: Edge = "bottom" if group == "upper_buccal" else "top"
        angle = _line_angle(_edge_points(teeth, edge), (h, w))
    level = (
        LevelEstimate(0.0, False)
        if angle is None
        else LevelEstimate(max(-MAX_LEVEL_DEG, min(MAX_LEVEL_DEG, angle)), True)
    )
    log.debug("level", slot=slot, angle=round(level.angle_deg, 2), confident=level.confident)
    return level


def normalize_view(
    rgb: RGBImage, slot: Slot, flip: Flip, cell_size: tuple[int, int]
) -> CellGeometry:
    """Flip, level, and crop `rgb` for its composite cell (spec §4.7.2)."""
    h, w = rgb.shape[:2]
    cell_w, cell_h = cell_size
    aspect = cell_w / cell_h
    small, scale = resize_long_edge(rgb, ANALYSIS_LONG_EDGE)
    flipped = flip_pixels(small, flip)
    level = estimate_level(flipped, slot)
    rotation = _rotation(level.angle_deg, w / 2, h / 2)
    # The crop must frame the teeth where they land after leveling, not where they were shot.
    small_h, small_w = flipped.shape[:2]
    leveled = cast(
        RGBImage,
        cv2.warpAffine(
            flipped,
            _rotation(level.angle_deg, small_w / 2, small_h / 2)[:2],
            (small_w, small_h),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REPLICATE,
        ),
    )
    valid = _valid_rect(level.angle_deg, w, h, aspect)
    crop_x, crop_y, crop_w, _ = _smart_crop(tooth_mask(leveled), 1 / scale, w, h, aspect, valid)
    to_cell = tf.scaling(cell_w / crop_w) @ tf.translation(-crop_x, -crop_y)
    photo_to_cell = to_cell @ rotation @ flip_matrix(flip, w, h)
    return CellGeometry(tf.to_normalized(photo_to_cell, w, cell_w), flip, level)


def _rotation(angle_deg: float, cx: float, cy: float) -> tf.Matrix:
    return (
        tf.translation(cx, cy) @ tf.similarity(1.0, angle_deg, 0.0, 0.0) @ tf.translation(-cx, -cy)
    )


def _symmetry_angle(teeth: BoolMask) -> float | None:
    """Rotation that makes the arch most mirror-symmetric about a vertical axis."""
    if teeth.mean() < _MIN_TOOTH_SHARE:
        return None
    h, w = teeth.shape
    rows, cols = np.nonzero(teeth)
    cx, cy = float(cols.mean()), float(rows.mean())
    mask = teeth.astype(np.uint8)
    mirror = np.array([[-1.0, 0.0, 2 * cx], [0.0, 1.0, 0.0]])
    angles = np.arange(-MAX_LEVEL_DEG, MAX_LEVEL_DEG + 1e-9, SYMMETRY_STEP_DEG)
    scores = np.empty(len(angles))
    for i, a in enumerate(angles):
        turn = cv2.getRotationMatrix2D((cx, cy), -float(a), 1)
        rotated = cv2.warpAffine(mask, turn, (w, h), flags=cv2.INTER_NEAREST) > 0
        mirrored = (
            cv2.warpAffine(rotated.astype(np.uint8), mirror, (w, h), flags=cv2.INTER_NEAREST) > 0
        )
        union = np.count_nonzero(rotated | mirrored)
        scores[i] = np.count_nonzero(rotated & mirrored) / union if union else 0.0
    best = int(np.argmax(scores))
    if scores[best] < MIN_SYMMETRY_IOU:
        return None
    offset = 0.0
    if 0 < best < len(scores) - 1:
        # The IoU peak is sharper than the 0.5° grid; a parabola through it recovers the rest.
        left, mid, right = (float(s) for s in scores[best - 1 : best + 2])
        denominator = left - 2 * mid + right
        if denominator < 0:
            offset = 0.5 * (left - right) / denominator
    return float(angles[best]) + offset * SYMMETRY_STEP_DEG


def _boundary_points(teeth: BoolMask, lightness: Lightness) -> FloatPoints:
    """Per column, the row where the upper arch meets the lower arch."""
    h, w = teeth.shape
    blurred = cast(Lightness, cv2.GaussianBlur(lightness, _DARK_ROW_BLUR, 0))
    points: list[tuple[float, float]] = []
    for x in range(0, w, COLUMN_STEP):
        rows = np.flatnonzero(teeth[:, x])
        if len(rows) < _MIN_COLUMN_SHARE * h:
            continue
        top, bottom = int(rows[0]), int(rows[-1])
        openings = np.flatnonzero(np.diff(rows) > 1)
        if len(openings):
            # Tilted gaps between neighboring teeth also cross the column; the arches meet
            # near the middle of the span, so the largest gap is not the right one.
            centers = (rows[openings] + rows[openings + 1]) / 2
            y = float(centers[np.argmin(np.abs(centers - (top + bottom) / 2))])
        else:
            # Arches touching close the opening in the mask; the shadow between them remains.
            span = bottom - top
            lo, hi = top + int(_DARK_ROW_SPAN[0] * span), top + int(_DARK_ROW_SPAN[1] * span)
            y = float(lo + int(np.argmin(blurred[lo : hi + 1, x])))
        points.append((float(x), y))
    return np.array(points, dtype=np.float64).reshape(-1, 2)


def _edge_points(teeth: BoolMask, edge: Edge) -> FloatPoints:
    """Per column, the topmost or bottommost tooth pixel."""
    h, w = teeth.shape
    columns = teeth[:, ::COLUMN_STEP]
    present = columns.any(axis=0)
    rows = np.argmax(columns, axis=0) if edge == "top" else h - 1 - np.argmax(columns[::-1], axis=0)
    xs = np.arange(0, w, COLUMN_STEP)
    return np.column_stack([xs[present], rows[present]]).astype(np.float64)


def _line_angle(points: FloatPoints, shape: tuple[int, int]) -> float | None:
    """Rotation that makes the dominant straight line through `points` horizontal."""
    h, w = shape
    n = len(points)
    # A RANSAC pair needs two points, whatever the width.
    if n < max(2.0, MIN_LINE_COLUMNS * w / COLUMN_STEP):
        return None
    x, y = points[:, 0], points[:, 1]
    rng = np.random.default_rng(0)
    first = rng.integers(0, n, RANSAC_TRIALS)
    second = rng.integers(0, n - 1, RANSAC_TRIALS)
    second += second >= first  # two distinct points per trial
    # One point per column, so two distinct points never share an x.
    slopes = (y[second] - y[first]) / (x[second] - x[first])
    predicted = y[first, None] + slopes[:, None] * (x[None, :] - x[first, None])
    inliers = np.abs(y[None, :] - predicted) <= LINE_TOLERANCE * h
    best = inliers[int(np.argmax(inliers.sum(axis=1)))]
    if best.mean() < MIN_LINE_INLIERS:
        return None
    slope = float(np.polyfit(x[best], y[best], 1)[0])
    return -math.degrees(math.atan(slope))


def _fit_aspect(w: float, h: float, aspect: float) -> tuple[float, float]:
    """Width and height of the largest rectangle of `aspect` that fits in w x h."""
    if w / h > aspect:
        return h * aspect, h
    return w, w / aspect


def _valid_rect(angle_deg: float, w: int, h: int, aspect: float) -> Rect:
    """Largest centered cell-aspect rectangle of the leveled frame that the photo fully covers."""
    fit_w, fit_h = _fit_aspect(w, h, aspect)
    to_photo = tf.invert(_rotation(angle_deg, w / 2, h / 2))

    def corners(s: float) -> FloatPoints:
        dx, dy = s * fit_w / 2, s * fit_h / 2
        return np.array(
            [
                [w / 2 - dx, h / 2 - dy],
                [w / 2 + dx, h / 2 - dy],
                [w / 2 + dx, h / 2 + dy],
                [w / 2 - dx, h / 2 + dy],
            ]
        )

    lo, hi = 0.0, 1.0
    for _ in range(_BISECTION_STEPS):
        mid = (lo + hi) / 2
        photo = tf.map_points(to_photo, corners(mid))
        if np.all(photo >= 0) and np.all(photo <= (w, h)):
            lo = mid
        else:
            hi = mid
    return w / 2 - lo * fit_w / 2, h / 2 - lo * fit_h / 2, lo * fit_w, lo * fit_h


def _smart_crop(
    teeth: BoolMask, to_photo: float, w: int, h: int, aspect: float, valid: Rect
) -> Rect:
    """Frame the teeth (spec §4.7.2 step 3) without leaving `valid`."""
    if teeth.mean() < _MIN_TOOTH_SHARE:
        return valid
    rows, cols = np.nonzero(teeth)
    left, right = (float(v) * to_photo for v in np.percentile(cols, CROP_PERCENTILES))
    top, bottom = (float(v) * to_photo for v in np.percentile(rows, CROP_PERCENTILES))
    center_x, center_y = (left + right) / 2, (top + bottom) / 2
    width = (right - left) * (1 + 2 * CROP_MARGIN)
    height = (bottom - top) * (1 + 2 * CROP_MARGIN)
    width = max(width, height * aspect)
    # Retractors and lips can hide most teeth; a tight box would then zoom far in.
    width = max(width, _fit_aspect(w, h, aspect)[0] / MAX_ZOOM)
    valid_x, valid_y, valid_w, valid_h = valid
    # No black corners wins over the zoom limit.
    width = min(width, valid_w)
    height = width / aspect
    center_x = min(max(center_x, valid_x + width / 2), valid_x + valid_w - width / 2)
    center_y = min(max(center_y, valid_y + height / 2), valid_y + valid_h - height / 2)
    return center_x - width / 2, center_y - height / 2, width, height
