"""Feature 1 pipeline without storage: align, color-match, and render one photo (spec §3.2)."""

from __future__ import annotations

from dataclasses import dataclass

import cv2

from dental_image_organizer.core import transform as tf
from dental_image_organizer.core.align import (
    WORKING_LONG_EDGE,
    AlignResult,
    PreparedReference,
    align_to_reference,
)
from dental_image_organizer.core.color import LabCurves, fit_lab_curves
from dental_image_organizer.core.imageio import RGBImage, resize_long_edge
from dental_image_organizer.core.render import coverage_mask, render

OUTPUT_SCALES = (1.0, 1.5, 2.0)


@dataclass(frozen=True)
class ProcedureEdit:
    transform: tf.Matrix
    curves: LabCurves | None
    align: AlignResult


def compute_edit(photo_rgb: RGBImage, reference: PreparedReference) -> ProcedureEdit:
    aligned = align_to_reference(photo_rgb, reference)
    curves = None
    # The center-fit fallback has no pixel correspondence, and fitting colors on unrelated
    # content would shift them arbitrarily, so it keeps the photo's own colors.
    if aligned.estimated:
        working, _ = resize_long_edge(photo_rgb, WORKING_LONG_EDGE)
        size = (reference.working.shape[1], reference.working.shape[0])
        warped = render(working, aligned.transform, size, interpolation=cv2.INTER_LINEAR)
        valid = coverage_mask((working.shape[1], working.shape[0]), aligned.transform, size)
        curves = fit_lab_curves(warped, reference.working, valid, reference.working_teeth)
    return ProcedureEdit(transform=aligned.transform, curves=curves, align=aligned)


def output_size(reference_size: tuple[int, int], output_scale: float) -> tuple[int, int]:
    if output_scale not in OUTPUT_SCALES:
        raise ValueError(f"output_scale must be one of {OUTPUT_SCALES}, got {output_scale}")
    w, h = reference_size
    return round(w * output_scale), round(h * output_scale)


def render_edit(photo_rgb: RGBImage, edit: ProcedureEdit, size: tuple[int, int]) -> RGBImage:
    return render(photo_rgb, edit.transform, size, curves=edit.curves)


def render_reference(reference_rgb: RGBImage, size: tuple[int, int]) -> RGBImage:
    if (reference_rgb.shape[1], reference_rgb.shape[0]) == size:
        return reference_rgb
    return render(reference_rgb, tf.identity(), size)
