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
    cells: dict[Slot, CellPlan]
    tray: tuple[int, ...]
    method: AssignMethod
    low_confidence: frozenset[Slot]
    baseline: Slot | None


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
) -> CompositePlan:
    if len(images) != len(assignment.slots):
        raise ValueError(f"got {len(images)} images for {len(assignment.slots)} assigned photos")
    slot_map = assignment.slot_map
    geometries: dict[Slot, CellGeometry] = {}
    for slot in SLOTS:
        if slot not in slot_map:
            continue
        photo = slot_map[slot]
        # core/ only sees pixels; binding the position tags its events without file names.
        with structlog.contextvars.bound_contextvars(photo=photo + 1):
            geometries[slot] = normalize_view(images[photo], slot, flips[slot], cell_size)

    chosen = baseline if baseline in geometries else next(iter(geometries), None)
    colors = _fit_colors(images, slot_map, geometries, chosen, cell_size)
    cells = {
        slot: CellPlan(
            slot, slot_map[slot], geometry, colors.get(slot), assignment.confidences[slot_map[slot]]
        )
        for slot, geometry in geometries.items()
    }
    tray = tuple(i for i, slot in enumerate(assignment.slots) if slot is None)
    return CompositePlan(cells, tray, assignment.method, assignment.low_confidence, chosen)


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
    baseline: Slot | None,
    cell_size: tuple[int, int],
) -> dict[Slot, LabAffine]:
    if baseline is None:
        return {}
    # Small renders keep the fit cheap, and the normalized transforms make them match the
    # full-size cells that are rendered later.
    width = COLOR_ANALYSIS_WIDTH
    size = (width, max(1, round(width * cell_size[1] / cell_size[0])))
    previews: dict[Slot, RGBImage] = {}
    regions: dict[Slot, BoolMask] = {}
    for slot, geometry in geometries.items():
        preview = render(
            images[slot_map[slot]], geometry.transform, size, interpolation=cv2.INTER_AREA
        )
        teeth = tooth_mask(preview)
        previews[slot] = preview
        regions[slot] = teeth | gingiva_mask(preview, teeth)
    return {
        slot: fit_reinhard(previews[slot], regions[slot], previews[baseline], regions[baseline])
        for slot in geometries
        if slot != baseline
    }
