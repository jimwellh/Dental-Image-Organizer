"""Align a raw photo to the dentist-edited reference photo (spec §4.5.1).

Coordinate frames: "full" is the full-resolution photo or reference in pixels. A _View is
the image that features are detected on (a working copy, possibly a tight crop or mirrored)
together with the matrix from full photo pixels into it. Every estimate is kept as one
full-photo -> full-reference pixel matrix and converted to normalized form at the end.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

import cv2
import numpy as np
import numpy.typing as npt
import structlog

from dental_image_organizer.core import transform as tf
from dental_image_organizer.core.imageio import RGBImage, resize_long_edge
from dental_image_organizer.core.masks import BoolMask, gingiva_mask, tooth_mask

log = structlog.get_logger(__name__)

WORKING_LONG_EDGE = 1600
ECC_LONG_EDGE = 800
SIFT_FEATURES = 4000
MIN_MASKED_KEYPOINTS = 100
TOOTH_MASK_MIN_SHARE = 0.01
TOOTH_MASK_DILATION = 0.03
RATIO_TEST = 0.75
RANSAC_THRESHOLD_PX = 3.0
RANSAC_ITERATIONS = 5000
RANSAC_CONFIDENCE = 0.995
MIRROR_RETRY_INLIERS = 25
TIGHT_CROP_SCALE = 2.0
TIGHT_CROP_MARGIN = 0.15
ECC_ITERATIONS = 100
ECC_EPSILON = 1e-6
ECC_GAUSS_SIZE = 5
ECC_MIN_MASK_SHARE = 0.05
HOMOGRAPHY_MIN_INLIERS = 60
HOMOGRAPHY_MIN_QUADRANTS = 3
HOMOGRAPHY_MIN_ECC_GAIN = 0.02
HOMOGRAPHY_MAX_CORNER_SHIFT = 0.08
INLIERS_FOR_FULL_CONFIDENCE = 80
CONFIDENCE_OK = 0.6
CONFIDENCE_REVIEW = 0.35
SCALE_LIMITS = (0.2, 5.0)
MAX_ROTATION_DEG = 45.0

AlignModel = Literal["similarity", "homography"]
AlignStatus = Literal["ok", "needs_review"]
FloatPoints = npt.NDArray[np.float64]
Gray = npt.NDArray[np.uint8]


@dataclass(frozen=True)
class AlignResult:
    """Photo -> reference mapping in normalized coordinates, with the evidence behind it."""

    transform: tf.Matrix
    model: AlignModel
    confidence: float
    status: AlignStatus
    estimated: bool  # False when the center-fit fallback was used
    inliers: int
    ecc_cc: float
    detail_ratio: float
    mirrored: bool
    tight_crop: bool
    homography_note: str | None  # why the similarity was kept; None when a homography is used
    message: str | None


@dataclass(frozen=True)
class PreparedReference:
    """Everything derived from the reference once, then reused for every photo of a case."""

    full_size: tuple[int, int]
    working: RGBImage
    working_scale: float
    working_teeth: BoolMask
    points: FloatPoints
    descriptors: npt.NDArray[np.float32] | None
    ecc_template: npt.NDArray[np.float32]
    ecc_scale: float
    ecc_mask: Gray


@dataclass(frozen=True)
class _View:
    image: RGBImage
    from_full: tf.Matrix


@dataclass(frozen=True)
class _Estimate:
    matrix: tf.Matrix  # full photo px -> full reference px, similarity
    view: _View
    inliers: int
    inlier_ref_points: FloatPoints  # reference working px
    src_points: FloatPoints  # all matches, view px
    dst_points: FloatPoints  # all matches, reference working px
    mirrored: bool


def prepare_reference(reference_rgb: RGBImage) -> PreparedReference:
    h, w = reference_rgb.shape[:2]
    working, working_scale = resize_long_edge(reference_rgb, WORKING_LONG_EDGE)
    points, descriptors = _features(working)
    ecc_image, ecc_scale = resize_long_edge(reference_rgb, ECC_LONG_EDGE)
    teeth = tooth_mask(ecc_image)
    region = teeth | gingiva_mask(ecc_image, teeth)
    if region.mean() < ECC_MIN_MASK_SHARE:
        region = np.ones_like(region)
    return PreparedReference(
        full_size=(w, h),
        working=working,
        working_scale=working_scale,
        working_teeth=tooth_mask(working),
        points=points,
        descriptors=descriptors,
        ecc_template=_ecc_gray(ecc_image),
        ecc_scale=ecc_scale,
        ecc_mask=region.astype(np.uint8) * 255,
    )


def align_to_reference(photo_rgb: RGBImage, reference: PreparedReference) -> AlignResult:
    working, photo_scale = resize_long_edge(photo_rgb, WORKING_LONG_EDGE)
    full_view = _View(working, tf.scaling(photo_scale))

    estimate = _estimate(full_view, reference, mirrored=False)
    if estimate is None or estimate.inliers < MIRROR_RETRY_INLIERS:
        mirrored = _estimate(full_view, reference, mirrored=True)
        if mirrored is not None and (estimate is None or mirrored.inliers > estimate.inliers):
            estimate = mirrored
    if estimate is None:
        return _fallback(photo_rgb, reference, "No matching features between photo and reference.")

    tight_crop = False
    if _working_scale(estimate.matrix, reference, photo_scale) > TIGHT_CROP_SCALE:
        crop = _crop_view(photo_rgb, estimate.matrix, reference.full_size)
        second = _estimate(crop, reference, mirrored=estimate.mirrored) if crop else None
        if second is not None and second.inliers > estimate.inliers:
            estimate, tight_crop = second, True

    similarity = _refine(estimate.matrix, estimate.view, reference, cv2.MOTION_EUCLIDEAN)
    params = tf.decompose_similarity(similarity)
    working_scale = _working_scale(similarity, reference, photo_scale)
    if not (SCALE_LIMITS[0] <= working_scale <= SCALE_LIMITS[1]) or (
        abs(params.angle_deg) > MAX_ROTATION_DEG
    ):
        log.debug(
            "align_rejected",
            inliers=estimate.inliers,
            scale=round(working_scale, 3),
            angle=round(params.angle_deg, 2),
            tight_crop=tight_crop,
            mirrored=estimate.mirrored,
        )
        return _fallback(photo_rgb, reference, "Estimated scale or rotation is implausible.")
    similarity_cc = _ecc_score(similarity, estimate.view, reference)

    model: AlignModel = "similarity"
    matrix, cc = similarity, similarity_cc
    homography, homography_cc, note = _try_homography(
        estimate, similarity, similarity_cc, reference
    )
    if homography is not None:
        matrix, model, cc = homography, "homography", homography_cc

    confidence = float(
        np.clip(
            0.5 * min(1.0, estimate.inliers / INLIERS_FOR_FULL_CONFIDENCE) + 0.5 * max(0.0, cc),
            0.0,
            1.0,
        )
    )
    log.debug(
        "align",
        inliers=estimate.inliers,
        ecc_cc=round(cc, 4),
        similarity_cc=round(similarity_cc, 4),
        scale=round(working_scale, 3),
        angle=round(params.angle_deg, 2),
        model=model,
        homography_note=note,
        tight_crop=tight_crop,
        mirrored=estimate.mirrored,
        confidence=round(confidence, 3),
    )
    if confidence < CONFIDENCE_REVIEW:
        return _fallback(photo_rgb, reference, "Alignment confidence is too low.")
    return AlignResult(
        transform=tf.to_normalized(matrix, photo_rgb.shape[1], reference.full_size[0]),
        model=model,
        confidence=confidence,
        status="ok" if confidence >= CONFIDENCE_OK else "needs_review",
        estimated=True,
        inliers=estimate.inliers,
        ecc_cc=cc,
        detail_ratio=1.0 / params.scale,
        mirrored=estimate.mirrored,
        tight_crop=tight_crop,
        homography_note=note,
        message=None if confidence >= CONFIDENCE_OK else "Low alignment confidence.",
    )


def homography_gate_failures(
    homography: tf.Matrix,
    similarity: tf.Matrix,
    frame_size: tuple[int, int],
    *,
    homography_cc: float,
    similarity_cc: float,
) -> list[str]:
    """Names of the acceptance gates (spec §4.5.1 step 6.3) that `homography` fails."""
    w, h = frame_size
    corners = np.array([[0.0, 0.0], [w, 0.0], [w, h], [0.0, h]])
    failures: list[str] = []
    if homography_cc - similarity_cc < HOMOGRAPHY_MIN_ECC_GAIN:
        failures.append("ecc_gain")
    inverse = tf.invert(homography)
    denominators = np.hstack([corners, np.ones((4, 1))]) @ inverse[2]
    photo_corners = tf.map_points(inverse, corners)
    shift = np.linalg.norm(tf.map_points(similarity, photo_corners) - corners, axis=1)
    if np.max(shift) > HOMOGRAPHY_MAX_CORNER_SHIFT * w:
        failures.append("corner_shift")
    # A non-positive w' means that frame point is mapped from behind the camera.
    behind_camera = bool(np.any(denominators <= 0))
    convex = not behind_camera and bool(
        cv2.isContourConvex(photo_corners.astype(np.float32).reshape(-1, 1, 2))
    )
    if not convex:
        failures.append("not_convex")
    log.debug(
        "homography_gates",
        homography_cc=round(homography_cc, 4),
        similarity_cc=round(similarity_cc, 4),
        ecc_gain=round(homography_cc - similarity_cc, 4),
        max_corner_shift=round(float(np.max(shift)) / w, 4),
        behind_camera=behind_camera,
        convex=convex,
        failed=failures,
    )
    return failures


def _try_homography(
    estimate: _Estimate,
    similarity: tf.Matrix,
    similarity_cc: float,
    reference: PreparedReference,
) -> tuple[tf.Matrix | None, float, str | None]:
    if estimate.inliers < HOMOGRAPHY_MIN_INLIERS:
        return None, similarity_cc, "too_few_inliers"
    if _quadrants(estimate.inlier_ref_points, reference.working) < HOMOGRAPHY_MIN_QUADRANTS:
        return None, similarity_cc, "poor_coverage"
    found, _ = cv2.findHomography(
        estimate.src_points.astype(np.float32),
        estimate.dst_points.astype(np.float32),
        cv2.RANSAC,
        RANSAC_THRESHOLD_PX,
    )
    if found is None:
        return None, similarity_cc, "estimation_failed"
    initial = tf.scaling(1.0 / reference.working_scale) @ found @ estimate.view.from_full
    homography = _refine(tf.normalize(initial), estimate.view, reference, cv2.MOTION_HOMOGRAPHY)
    homography_cc = _ecc_score(homography, estimate.view, reference)
    failures = homography_gate_failures(
        homography,
        similarity,
        reference.full_size,
        homography_cc=homography_cc,
        similarity_cc=similarity_cc,
    )
    if failures:
        return None, similarity_cc, ",".join(failures)
    return homography, homography_cc, None


def _estimate(view: _View, reference: PreparedReference, *, mirrored: bool) -> _Estimate | None:
    if mirrored:
        width = view.image.shape[1]
        flip = np.array([[-1.0, 0.0, width - 1.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
        view = _View(np.ascontiguousarray(view.image[:, ::-1]), flip @ view.from_full)
    points, descriptors = _features(view.image)
    query, train = _match(descriptors, reference.descriptors)
    if len(query) < 4:
        return None
    src, dst = points[query], reference.points[train]
    affine, inlier_flags = cv2.estimateAffinePartial2D(
        src.astype(np.float32),
        dst.astype(np.float32),
        method=cv2.RANSAC,
        ransacReprojThreshold=RANSAC_THRESHOLD_PX,
        maxIters=RANSAC_ITERATIONS,
        confidence=RANSAC_CONFIDENCE,
    )
    if affine is None:
        return None
    inliers = inlier_flags.ravel().astype(bool)
    matrix = tf.scaling(1.0 / reference.working_scale) @ tf.from_affine(affine) @ view.from_full
    return _Estimate(
        matrix=matrix,
        view=view,
        inliers=int(inliers.sum()),
        inlier_ref_points=dst[inliers],
        src_points=src,
        dst_points=dst,
        mirrored=mirrored,
    )


def _features(rgb: RGBImage) -> tuple[FloatPoints, npt.NDArray[np.float32] | None]:
    gray = _clahe_lightness(rgb)
    sift = cv2.SIFT.create(nfeatures=SIFT_FEATURES)
    # Teeth survive surgery while soft tissue does not, so detection is focused on them.
    mask = _detection_mask(rgb)
    keypoints, descriptors = sift.detectAndCompute(gray, mask)
    if mask is not None and len(keypoints) < MIN_MASKED_KEYPOINTS:
        keypoints, descriptors = sift.detectAndCompute(gray, None)
    points = np.array([kp.pt for kp in keypoints], dtype=np.float64).reshape(-1, 2)
    return points, None if descriptors is None else np.asarray(descriptors, dtype=np.float32)


def _detection_mask(rgb: RGBImage) -> Gray | None:
    teeth = tooth_mask(rgb)
    if teeth.mean() < TOOTH_MASK_MIN_SHARE:
        return None
    k = max(3, round(min(rgb.shape[:2]) * TOOTH_MASK_DILATION)) | 1
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    return cast(Gray, cv2.dilate(teeth.astype(np.uint8) * 255, kernel))


def _match(
    query: npt.NDArray[np.float32] | None, train: npt.NDArray[np.float32] | None
) -> tuple[npt.NDArray[np.intp], npt.NDArray[np.intp]]:
    empty = np.zeros(0, dtype=np.intp)
    if query is None or train is None or len(query) < 2 or len(train) < 2:
        return empty, empty
    matcher = cv2.BFMatcher(cv2.NORM_L2)
    backward = {m.queryIdx: m.trainIdx for m in matcher.match(train, query)}
    pairs = [
        (pair[0].queryIdx, pair[0].trainIdx)
        for pair in matcher.knnMatch(query, train, k=2)
        if len(pair) == 2
        and pair[0].distance < RATIO_TEST * pair[1].distance
        and backward.get(pair[0].trainIdx) == pair[0].queryIdx
    ]
    if not pairs:
        return empty, empty
    q, t = zip(*pairs, strict=True)
    return np.array(q, dtype=np.intp), np.array(t, dtype=np.intp)


def _refine(matrix: tf.Matrix, view: _View, reference: PreparedReference, motion: int) -> tf.Matrix:
    """ECC refinement; on failure the input estimate is kept unchanged."""
    warped, mask = _warp_for_ecc(matrix, view, reference)
    if mask is None:
        return matrix
    size = 3 if motion == cv2.MOTION_HOMOGRAPHY else 2
    initial = np.eye(size, 3, dtype=np.float32)
    criteria = (cv2.TERM_CRITERIA_COUNT | cv2.TERM_CRITERIA_EPS, ECC_ITERATIONS, ECC_EPSILON)
    try:
        _, found = cv2.findTransformECC(
            reference.ecc_template, warped, initial, motion, criteria, mask, ECC_GAUSS_SIZE
        )
    except cv2.error as exc:
        log.debug("ecc_failed", motion=motion, error=str(exc).splitlines()[-1])
        return matrix
    warp = np.asarray(found, dtype=np.float64)
    w3 = warp if size == 3 else tf.from_affine(warp)
    # ECC maps template coordinates into the pre-warped photo: warped(W x) ~ template(x).
    ecc = tf.scaling(reference.ecc_scale)
    return tf.normalize(tf.invert(ecc) @ tf.invert(w3) @ ecc @ matrix)


def _ecc_score(matrix: tf.Matrix, view: _View, reference: PreparedReference) -> float:
    warped, mask = _warp_for_ecc(matrix, view, reference)
    if mask is None:
        return 0.0
    try:
        return float(
            cv2.computeECC(
                cv2.GaussianBlur(reference.ecc_template, (ECC_GAUSS_SIZE, ECC_GAUSS_SIZE), 0),
                cv2.GaussianBlur(warped, (ECC_GAUSS_SIZE, ECC_GAUSS_SIZE), 0),
                mask,
            )
        )
    except cv2.error:
        return 0.0


def _warp_for_ecc(
    matrix: tf.Matrix, view: _View, reference: PreparedReference
) -> tuple[npt.NDArray[np.float32], Gray | None]:
    h, w = reference.ecc_template.shape
    to_ecc = tf.scaling(reference.ecc_scale) @ matrix @ tf.invert(view.from_full)
    warped = cv2.warpPerspective(_ecc_gray(view.image), to_ecc, (w, h), flags=cv2.INTER_LINEAR)
    covered = cv2.warpPerspective(
        np.full(view.image.shape[:2], 255, np.uint8), to_ecc, (w, h), flags=cv2.INTER_NEAREST
    )
    mask = cv2.bitwise_and(reference.ecc_mask, covered)
    if cv2.countNonZero(mask) < ECC_MIN_MASK_SHARE * w * h:
        return cast(npt.NDArray[np.float32], warped), None
    return cast(npt.NDArray[np.float32], warped), cast(Gray, mask)


def _crop_view(
    photo_rgb: RGBImage, matrix: tf.Matrix, reference_size: tuple[int, int]
) -> _View | None:
    """Full-resolution crop around the region the reference shows (tight-crop second pass)."""
    w, h = reference_size
    corners = tf.map_points(tf.invert(matrix), [[0, 0], [w, 0], [w, h], [0, h]])
    (x0, y0), (x1, y1) = corners.min(axis=0), corners.max(axis=0)
    mx, my = (x1 - x0) * TIGHT_CROP_MARGIN, (y1 - y0) * TIGHT_CROP_MARGIN
    height, width = photo_rgb.shape[:2]
    left, top = max(0, int(x0 - mx)), max(0, int(y0 - my))
    right, bottom = min(width, int(np.ceil(x1 + mx))), min(height, int(np.ceil(y1 + my)))
    if right - left < 32 or bottom - top < 32:
        return None
    crop, scale = resize_long_edge(
        photo_rgb[top:bottom, left:right], WORKING_LONG_EDGE, upscale=True
    )
    return _View(crop, tf.scaling(scale) @ tf.translation(-left, -top))


def _working_scale(matrix: tf.Matrix, reference: PreparedReference, photo_scale: float) -> float:
    """Scale from the full photo's working copy to the reference's working copy."""
    return tf.decompose_similarity(matrix).scale * reference.working_scale / photo_scale


def _quadrants(points: FloatPoints, image: RGBImage) -> int:
    h, w = image.shape[:2]
    right = points[:, 0] >= w / 2
    bottom = points[:, 1] >= h / 2
    return len(set(zip(right.tolist(), bottom.tolist(), strict=True)))


def _fallback(photo_rgb: RGBImage, reference: PreparedReference, message: str) -> AlignResult:
    """Center the photo so it covers the reference frame; the user aligns it manually."""
    ph, pw = photo_rgb.shape[:2]
    rw, rh = reference.full_size
    k = max(rw / pw, rh / ph)
    matrix = tf.similarity(k, 0.0, (rw - k * pw) / 2, (rh - k * ph) / 2)
    log.debug("align_fallback", reason=message)
    return AlignResult(
        transform=tf.to_normalized(matrix, pw, rw),
        model="similarity",
        confidence=0.0,
        status="needs_review",
        estimated=False,
        inliers=0,
        ecc_cc=0.0,
        detail_ratio=1.0 / k,
        mirrored=False,
        tight_crop=False,
        homography_note=None,
        message=message,
    )


def _clahe_lightness(rgb: RGBImage) -> Gray:
    lightness = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)[..., 0]
    return cast(Gray, cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(lightness))


def _ecc_gray(rgb: RGBImage) -> npt.NDArray[np.float32]:
    lightness = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)[..., 0]
    return lightness.astype(np.float32) / 255.0
