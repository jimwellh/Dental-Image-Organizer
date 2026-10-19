"""Color matching in CIE Lab.

Two methods share the Lab conversions: monotone per-channel curves that match a procedure photo
to its reference (spec §4.5.2), and an affine Reinhard mean/spread transfer that unifies the
cells of a composite (spec §4.7.3).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, cast

import cv2
import numpy as np
import numpy.typing as npt

from dental_image_organizer.core.imageio import RGBImage

METHOD = "lab_quantile_v1"
CHANNELS = ("L", "a", "b")
QUANTILES = np.linspace(0.0, 1.0, 17)
CHANNEL_LIMITS = (25.0, 15.0, 15.0)
TOOTH_WEIGHT = 1.0
OTHER_WEIGHT = 0.25
MIN_TOOTH_SHARE = 0.03
MAX_ITERATIONS = 3
MAX_EXCLUDED_SHARE = 0.5
MAD_K = 3.0
MAD_TO_SIGMA = 1.4826
STABLE_SHARE = 0.01
MAX_FIT_PIXELS = 250_000
APPLY_CHUNK_ROWS = 512

REINHARD_METHOD = "lab_reinhard_v1"
REINHARD_STRENGTH = 0.8
REINHARD_MEAN_LIMITS = (12.0, 8.0, 8.0)
# Guards a near-zero source spread; the spec does not bound the gain.
REINHARD_GAIN_RANGE = (0.67, 1.5)
MIN_STATS_SHARE = 0.05
MIN_SPREAD = 1e-6

LabImage = npt.NDArray[np.float32]
FloatArray = npt.NDArray[np.float64]
BoolMask = npt.NDArray[np.bool_]


def to_lab(rgb: RGBImage) -> LabImage:
    """sRGB uint8 to Lab float32 with L in [0, 100] and a*, b* in their natural units."""
    return cast(LabImage, cv2.cvtColor(rgb.astype(np.float32) / 255.0, cv2.COLOR_RGB2LAB))


def from_lab(lab: LabImage) -> RGBImage:
    rgb = cv2.cvtColor(lab.astype(np.float32), cv2.COLOR_LAB2RGB)
    return np.clip(np.rint(rgb * 255.0), 0, 255).astype(np.uint8)


class LabTransform(Protocol):
    def apply_lab(self, lab: npt.NDArray[np.floating[Any]]) -> npt.NDArray[np.float32]: ...


@dataclass(frozen=True)
class Curve:
    src: tuple[float, ...]
    dst: tuple[float, ...]

    def apply(self, values: npt.NDArray[np.float32]) -> npt.NDArray[np.float32]:
        src, dst = np.asarray(self.src), np.asarray(self.dst)
        out = np.interp(values, src, dst)
        # Beyond the fitted range keep the end offsets instead of clamping, so highlights and
        # shadows outside the overlap region do not flatten into one value.
        low, high = values < src[0], values > src[-1]
        out[low] = values[low] + (dst[0] - src[0])
        out[high] = values[high] + (dst[-1] - src[-1])
        return np.asarray(out, dtype=np.float32)


@dataclass(frozen=True)
class LabCurves:
    curves: tuple[Curve, Curve, Curve]

    def apply_lab(self, lab: npt.NDArray[np.floating[Any]]) -> npt.NDArray[np.float32]:
        out = np.empty(lab.shape, dtype=np.float32)
        for c, curve in enumerate(self.curves):
            out[..., c] = curve.apply(lab[..., c].astype(np.float32))
        return out

    def to_params(self) -> dict[str, dict[str, list[float]]]:
        return {
            name: {"src": list(curve.src), "dst": list(curve.dst)}
            for name, curve in zip(CHANNELS, self.curves, strict=True)
        }

    @classmethod
    def from_params(cls, params: dict[str, dict[str, list[float]]]) -> LabCurves:
        curves = tuple(
            Curve(tuple(params[name]["src"]), tuple(params[name]["dst"])) for name in CHANNELS
        )
        return cls(cast(tuple[Curve, Curve, Curve], curves))


@dataclass(frozen=True)
class LabAffine:
    gains: tuple[float, float, float]
    offsets: tuple[float, float, float]

    def apply_lab(self, lab: npt.NDArray[np.floating[Any]]) -> npt.NDArray[np.float32]:
        gains = np.asarray(self.gains, dtype=np.float32)
        offsets = np.asarray(self.offsets, dtype=np.float32)
        return np.asarray(lab.astype(np.float32) * gains + offsets, dtype=np.float32)

    def to_params(self) -> dict[str, dict[str, float]]:
        return {
            name: {"gain": gain, "offset": offset}
            for name, gain, offset in zip(CHANNELS, self.gains, self.offsets, strict=True)
        }

    @classmethod
    def from_params(cls, params: dict[str, dict[str, float]]) -> LabAffine:
        gains = tuple(float(params[name]["gain"]) for name in CHANNELS)
        offsets = tuple(float(params[name]["offset"]) for name in CHANNELS)
        return cls(
            cast(tuple[float, float, float], gains), cast(tuple[float, float, float], offsets)
        )


def fit_lab_curves(
    photo_rgb: RGBImage,
    reference_rgb: RGBImage,
    valid: BoolMask,
    tooth: BoolMask | None = None,
) -> LabCurves:
    """Fit curves mapping the aligned photo's colors onto the reference's.

    `photo_rgb` must already be warped into the reference frame; `valid` marks pixels the
    photo actually covers. `tooth` is the reference's tooth mask and up-weights stable teeth.
    """
    # An evenly spaced subsample keeps the quantiles stable while bounding the sort cost.
    rows = np.flatnonzero(valid)
    rows = rows[np.linspace(0, len(rows) - 1, min(len(rows), MAX_FIT_PIXELS)).astype(np.intp)]
    photo = to_lab(photo_rgb).reshape(-1, 3)[rows].astype(np.float64)
    reference = to_lab(reference_rgb).reshape(-1, 3)[rows].astype(np.float64)
    teeth = tooth.ravel()[rows] if tooth is not None else np.zeros(len(rows), dtype=bool)
    weights = (
        np.where(teeth, TOOTH_WEIGHT, OTHER_WEIGHT)
        if teeth.mean() >= MIN_TOOTH_SHARE
        else np.ones(len(photo))
    )
    included = np.ones(len(photo), dtype=bool)
    curves = _fit(photo, reference, weights, included)
    for _ in range(MAX_ITERATIONS):
        residual = np.linalg.norm(curves.apply_lab(photo) - reference, axis=1)
        updated = _keep_low_residuals(residual)
        changed = float(np.mean(updated != included))
        included = updated
        curves = _fit(photo, reference, weights, included)
        if changed < STABLE_SHARE:
            break
    return curves


def apply_lab_transform(
    rgb: RGBImage, transform: LabTransform, *, chunk_rows: int = APPLY_CHUNK_ROWS
) -> RGBImage:
    # Row chunks bound memory: a full 48 MP image in float32 Lab would need about 580 MB.
    out = np.empty_like(rgb)
    for y in range(0, rgb.shape[0], chunk_rows):
        lab = to_lab(rgb[y : y + chunk_rows])
        out[y : y + chunk_rows] = from_lab(transform.apply_lab(lab))
    return out


def fit_reinhard(
    source_rgb: RGBImage, source_mask: BoolMask, target_rgb: RGBImage, target_mask: BoolMask
) -> LabAffine:
    """Per-channel mean/spread transfer of the source's colors onto the target's (spec §4.7.3)."""
    # A near-empty mask gives unstable statistics, so both sides fall back to the whole frame
    # together: whole-frame statistics against tooth and gingiva ones would shift the cell by
    # the difference between the regions, not between the photos.
    masked = min(source_mask.mean(), target_mask.mean()) >= MIN_STATS_SHARE
    source = _lab_pixels(source_rgb, source_mask if masked else None)
    target = _lab_pixels(target_rgb, target_mask if masked else None)
    mu_s, mu_t = source.mean(axis=0), target.mean(axis=0)
    sd_s, sd_t = source.std(axis=0), target.std(axis=0)
    gains: list[float] = []
    offsets: list[float] = []
    for c in range(3):
        k = float(np.clip(sd_t[c] / sd_s[c], *REINHARD_GAIN_RANGE)) if sd_s[c] > MIN_SPREAD else 1.0
        limit = REINHARD_MEAN_LIMITS[c]
        shift = float(np.clip(mu_t[c] - mu_s[c], -limit, limit))
        # Blending the full transfer x' = (x - mu_s) * k + mu_s + shift with identity is affine.
        gains.append(1.0 - REINHARD_STRENGTH + REINHARD_STRENGTH * k)
        offsets.append(REINHARD_STRENGTH * (float(mu_s[c]) + shift - k * float(mu_s[c])))
    return LabAffine(
        cast(tuple[float, float, float], tuple(gains)),
        cast(tuple[float, float, float], tuple(offsets)),
    )


def _lab_pixels(rgb: RGBImage, mask: BoolMask | None) -> FloatArray:
    lab = to_lab(rgb)
    if mask is None:
        return lab.reshape(-1, 3).astype(np.float64)
    return lab[mask].astype(np.float64)


def _fit(
    photo: FloatArray, reference: FloatArray, weights: FloatArray, included: BoolMask
) -> LabCurves:
    w = weights[included]
    curves = tuple(
        _curve(
            _weighted_quantiles(photo[included, c], w),
            _weighted_quantiles(reference[included, c], w),
            CHANNEL_LIMITS[c],
        )
        for c in range(3)
    )
    return LabCurves(cast(tuple[Curve, Curve, Curve], curves))


def _weighted_quantiles(values: FloatArray, weights: FloatArray) -> FloatArray:
    order = np.argsort(values, kind="stable")
    v, w = values[order], weights[order]
    cumulative = np.cumsum(w)
    positions = (cumulative - 0.5 * w) / cumulative[-1]
    return np.interp(QUANTILES, positions, v)


def _curve(src_q: FloatArray, dst_q: FloatArray, limit: float) -> Curve:
    # np.interp needs strictly increasing x, and quantiles repeat on flat regions.
    src = np.maximum.accumulate(src_q) + np.arange(len(src_q)) * 1e-6
    dst = np.maximum.accumulate(np.clip(dst_q, src - limit, src + limit))
    return Curve(tuple(float(x) for x in src), tuple(float(x) for x in dst))


def _keep_low_residuals(residual: FloatArray) -> BoolMask:
    median = float(np.median(residual))
    mad = float(np.median(np.abs(residual - median)))
    keep = residual <= median + MAD_K * MAD_TO_SIGMA * mad
    if np.mean(~keep) > MAX_EXCLUDED_SHARE:
        keep = residual <= median
    return keep
