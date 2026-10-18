"""Plan and render a 3 x 3 composite from assigned photos (spec §4.6, §4.7)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

import cv2
import structlog

from dental_image_organizer.core.color import LabAffine, fit_reinhard
from dental_image_organizer.core.geometry import CellGeometry, normalize_view
from dental_image_organizer.core.imageio import RGBImage
from dental_image_organizer.core.layout import render_composite
from dental_image_organizer.core.masks import BoolMask, gingiva_mask, tooth_mask
from dental_image_organizer.core.render import render
from dental_image_organizer.core.slots import SLOTS, Flip, Slot
from dental_image_organizer.ml.assignment import (
    Assignment,
    AssignMethod,
    assign_by_classifier,
    assign_by_order,
)
from dental_image_organizer.ml.classifier import SlotClassifier
from dental_image_organizer.ml.embedder import EmbeddingPair

log = structlog.get_logger(__name__)

DEFAULT_CELL_SIZE = (1200, 800)
DEFAULT_GAP_PX = 0
DEFAULT_GAP_COLOR = (0, 0, 0)
DEFAULT_BASELINE: Slot = "occlusion_front"
COLOR_ANALYSIS_WIDTH = 360


@dataclass(frozen=True)
class CellPlan:
    """`color` is None for the baseline cell, which every other cell is matched to."""

    slot: Slot
    photo: int
    geometry: CellGeometry
    color: LabAffine | None
    confidence: float | None


@dataclass(frozen=True)
class CompositePlan:
    """`failed` maps each photo whose planning raised to its exception; its slot has no cell."""

    cells: dict[Slot, CellPlan]
    tray: tuple[int, ...]
    method: AssignMethod
    low_confidence: frozenset[Slot]
    baseline: Slot | None
    failed: dict[int, Exception]


def assign_photos(
    capture_times: Sequence[datetime],
    order: Sequence[Slot],
    *,
    classifier: SlotClassifier | None = None,
    embeddings: EmbeddingPair | None = None,
) -> Assignment:
    # A classifier without embeddings (model not available) cannot score anything, so the
    # shooting order is the only honest fallback.
    if classifier is not None and embeddings is not None:
        return assign_by_classifier(classifier.slot_probabilities(embeddings))
    return assign_by_order(capture_times, order)


def plan_composite(
    images: Sequence[RGBImage],
    assignment: Assignment,
    *,
    flips: Mapping[Slot, Flip],
    baseline: Slot = DEFAULT_BASELINE,
    cell_size: tuple[int, int] = DEFAULT_CELL_SIZE,
    positions: Sequence[int] | None = None,
) -> CompositePlan:
    """`positions` label each image's log events, 1-based; by default its index + 1.

    A batch passes the input positions, which count skipped and unreadable files too.
    """
    if len(images) != len(assignment.slots):
        raise ValueError(f"got {len(images)} images for {len(assignment.slots)} assigned photos")
    labels = list(positions) if positions is not None else list(range(1, len(images) + 1))
    if len(labels) != len(images):
        raise ValueError(f"got {len(labels)} positions for {len(images)} images")
    slot_map = assignment.slot_map
    failed: dict[int, Exception] = {}
    geometries: dict[Slot, CellGeometry] = {}
    for slot in SLOTS:
        if slot not in slot_map:
            continue
        photo, flip = slot_map[slot], flips[slot]
        # core/ only sees pixels; binding the position tags its events without file names.
        with structlog.contextvars.bound_contextvars(photo=labels[photo]):
            try:
                geometries[slot] = normalize_view(images[photo], slot, flip, cell_size)
            except Exception as exc:
                # A photo that breaks leveling, cropping, or its color fit leaves only its cell
                # empty; it never aborts the composite (spec §4.13). The trace is for the cause.
                log.exception("photo_failed")
                failed[photo] = exc

    chosen, colors, color_failed = _fit_colors(
        images, slot_map, geometries, baseline, cell_size, labels
    )
    failed |= color_failed
    cells = {
        slot: CellPlan(
            slot, slot_map[slot], geometry, colors.get(slot), assignment.confidences[slot_map[slot]]
        )
        for slot, geometry in geometries.items()
        if slot_map[slot] not in failed
    }
    tray = tuple(i for i, slot in enumerate(assignment.slots) if slot is None)
    return CompositePlan(cells, tray, assignment.method, assignment.low_confidence, chosen, failed)


def render_cell(image: RGBImage, cell: CellPlan, cell_size: tuple[int, int]) -> RGBImage:
    return render(image, cell.geometry.transform, cell_size, color=cell.color)


def compose(
    cells: Mapping[Slot, RGBImage],
    cell_size: tuple[int, int] = DEFAULT_CELL_SIZE,
    *,
    gap_px: int = DEFAULT_GAP_PX,
    gap_color: tuple[int, int, int] = DEFAULT_GAP_COLOR,
) -> RGBImage:
    return render_composite(cells, cell_size, gap_px=gap_px, gap_color=gap_color)


def _fit_colors(
    images: Sequence[RGBImage],
    slot_map: Mapping[Slot, int],
    geometries: Mapping[Slot, CellGeometry],
    baseline: Slot,
    cell_size: tuple[int, int],
    labels: Sequence[int],
) -> tuple[Slot | None, dict[Slot, LabAffine], dict[int, Exception]]:
    """Match each cell to the baseline: returns the baseline used, the fits, and the failures."""
    # Small renders keep the fit cheap, and the normalized transforms make them match the
    # full-size cells that are rendered later.
    width = COLOR_ANALYSIS_WIDTH
    size = (width, max(1, round(width * cell_size[1] / cell_size[0])))
    failed: dict[int, Exception] = {}
    previews: dict[Slot, RGBImage] = {}
    regions: dict[Slot, BoolMask] = {}
    for slot, geometry in geometries.items():
        photo = slot_map[slot]
        with structlog.contextvars.bound_contextvars(photo=labels[photo]):
            try:
                preview = render(
                    images[photo], geometry.transform, size, interpolation=cv2.INTER_AREA
                )
                teeth = tooth_mask(preview)
                region = teeth | gingiva_mask(preview, teeth)
            except Exception as exc:
                log.exception("photo_failed")
                failed[photo] = exc
            else:
                previews[slot], regions[slot] = preview, region

    # The requested baseline if it survived, else the first filled slot in grid order.
    chosen = baseline if baseline in previews else next(iter(previews), None)
    if chosen is None:
        return None, {}, failed
    colors: dict[Slot, LabAffine] = {}
    for slot in previews:
        if slot == chosen:
            continue
        photo = slot_map[slot]
        with structlog.contextvars.bound_contextvars(photo=labels[photo]):
            try:
                colors[slot] = fit_reinhard(
                    previews[slot], regions[slot], previews[chosen], regions[chosen]
                )
            except Exception as exc:
                log.exception("photo_failed")
                failed[photo] = exc
    return chosen, colors, failed
