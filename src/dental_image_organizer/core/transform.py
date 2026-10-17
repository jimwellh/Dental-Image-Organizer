"""Projective 3 x 3 transforms in normalized coordinates (spec §4.8).

Points are divided by the image width on both axes, so one stored matrix is valid for previews
and full-resolution renders alike.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

Matrix = npt.NDArray[np.float64]
Points = npt.NDArray[np.float64]

_MIRROR_X = np.diag([-1.0, 1.0, 1.0])


@dataclass(frozen=True)
class SimilarityParams:
    scale: float
    angle_deg: float
    tx: float
    ty: float
    mirror: bool


def identity() -> Matrix:
    return np.eye(3, dtype=np.float64)


def scaling(s: float) -> Matrix:
    return np.diag([s, s, 1.0])


def translation(tx: float, ty: float) -> Matrix:
    m = identity()
    m[0, 2], m[1, 2] = tx, ty
    return m


def similarity(
    scale: float, angle_deg: float, tx: float, ty: float, *, mirror: bool = False
) -> Matrix:
    """x' = scale * R(angle) * F * x + t, where F mirrors the x axis when `mirror` is set."""
    a = math.radians(angle_deg)
    c, s = scale * math.cos(a), scale * math.sin(a)
    m = np.array([[c, -s, tx], [s, c, ty], [0.0, 0.0, 1.0]])
    return m @ _MIRROR_X if mirror else m


def from_affine(m: npt.ArrayLike) -> Matrix:
    affine = np.asarray(m, dtype=np.float64).reshape(2, 3)
    return np.vstack([affine, [0.0, 0.0, 1.0]])


def normalize(h: Matrix) -> Matrix:
    return h / float(h[2, 2])


def invert(h: Matrix) -> Matrix:
    return normalize(np.linalg.inv(h))


def map_points(h: Matrix, points: npt.ArrayLike) -> Points:
    p = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    hom = np.hstack([p, np.ones((len(p), 1))]) @ h.T
    return hom[:, :2] / hom[:, 2:3]


def to_pixel(h_norm: Matrix, in_width: float, out_width: float) -> Matrix:
    """Convert a normalized in->out matrix to pixel units for the given image widths."""
    return scaling(out_width) @ h_norm @ scaling(1.0 / in_width)


def to_normalized(h_px: Matrix, in_width: float, out_width: float) -> Matrix:
    return scaling(1.0 / out_width) @ h_px @ scaling(in_width)


def decompose_similarity(h: Matrix) -> SimilarityParams:
    """Read scale, rotation, translation, and mirror from the affine part of `h`."""
    m = normalize(h)
    mirror = bool(np.linalg.det(m[:2, :2]) < 0)
    proper = m @ _MIRROR_X if mirror else m
    a, b = proper[0, 0], proper[1, 0]
    return SimilarityParams(
        scale=float(math.hypot(a, b)),
        angle_deg=float(math.degrees(math.atan2(b, a))),
        tx=float(m[0, 2]),
        ty=float(m[1, 2]),
        mirror=mirror,
    )


def fit_similarity(src: npt.ArrayLike, dst: npt.ArrayLike) -> Matrix:
    """Least-squares similarity mapping src onto dst; a mirror is used only if it fits better."""
    s = np.asarray(src, dtype=np.float64).reshape(-1, 2)
    d = np.asarray(dst, dtype=np.float64).reshape(-1, 2)
    best: Matrix | None = None
    best_error = math.inf
    for mirror in (False, True):
        candidate = _umeyama(s * [-1.0, 1.0], d) @ _MIRROR_X if mirror else _umeyama(s, d)
        error = float(np.sum((map_points(candidate, s) - d) ** 2))
        if error < best_error - 1e-12:
            best, best_error = candidate, error
    assert best is not None
    return best


def closest_similarity(h: Matrix, frame_width: float, frame_height: float) -> Matrix:
    """Similarity that best reproduces where `h` sends the output frame's four corners.

    Used by "Reset perspective" (spec §4.8): the fit is done on the photo points that `h`
    maps onto the output corners.
    """
    corners = np.array(
        [[0.0, 0.0], [frame_width, 0.0], [frame_width, frame_height], [0.0, frame_height]]
    )
    photo_points = map_points(invert(h), corners)
    return fit_similarity(photo_points, corners)


def _umeyama(src: Points, dst: Points) -> Matrix:
    mu_s, mu_d = src.mean(axis=0), dst.mean(axis=0)
    xs, xd = src - mu_s, dst - mu_d
    u, sigma, vt = np.linalg.svd(xd.T @ xs / len(src))
    d = np.diag([1.0, float(np.sign(np.linalg.det(u) * np.linalg.det(vt))) or 1.0])
    rotation = u @ d @ vt
    variance = float((xs**2).sum() / len(src))
    scale = float(np.trace(np.diag(sigma) @ d)) / variance
    t = mu_d - scale * rotation @ mu_s
    m = identity()
    m[:2, :2] = scale * rotation
    m[:2, 2] = t
    return m
