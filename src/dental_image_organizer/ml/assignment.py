"""Photo-to-slot assignment: classifier, shooting order, or by hand (spec §4.6)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import numpy as np
import numpy.typing as npt
from scipy.optimize import linear_sum_assignment

from dental_image_organizer.core.slots import SLOTS, Slot

LOW_CONFIDENCE = 0.5
_EPSILON = 1e-6  # keeps log finite when a probability is exactly zero

AssignMethod = Literal["classifier", "order"]


@dataclass(frozen=True)
class Assignment:
    """Index i is photo i; None means the tray."""

    slots: tuple[Slot | None, ...]
    confidences: tuple[float | None, ...]
    method: AssignMethod

    @property
    def slot_map(self) -> dict[Slot, int]:
        return {slot: i for i, slot in enumerate(self.slots) if slot is not None}

    @property
    def low_confidence(self) -> frozenset[Slot]:
        return frozenset(
            slot
            for slot, confidence in zip(self.slots, self.confidences, strict=True)
            if slot is not None and confidence is not None and confidence < LOW_CONFIDENCE
        )


def assign_by_classifier(probabilities: npt.NDArray[np.float64]) -> Assignment:
    if probabilities.ndim != 2 or probabilities.shape[1] != len(SLOTS):
        raise ValueError(
            f"expected probabilities of shape (N, {len(SLOTS)}), got {probabilities.shape}"
        )
    # A global optimum, not a per-photo argmax: two photos must never claim one slot.
    rows, cols = linear_sum_assignment(-np.log(probabilities + _EPSILON))
    slots: list[Slot | None] = [None] * len(probabilities)
    confidences: list[float | None] = [None] * len(probabilities)
    for row, col in zip(rows.tolist(), cols.tolist(), strict=True):
        slots[row] = SLOTS[col]
        confidences[row] = float(probabilities[row, col])
    return Assignment(tuple(slots), tuple(confidences), "classifier")


def assign_by_order(capture_times: Sequence[datetime], order: Sequence[Slot]) -> Assignment:
    if sorted(order) != sorted(SLOTS):
        raise ValueError("order must list every slot exactly once")
    # EXIF times are local wall-clock; mixing aware and naive values would make them incomparable.
    ranked = sorted(
        range(len(capture_times)), key=lambda i: (capture_times[i].replace(tzinfo=None), i)
    )
    slots: list[Slot | None] = [None] * len(capture_times)
    for slot, photo in zip(order, ranked, strict=False):
        slots[photo] = slot
    return Assignment(tuple(slots), (None,) * len(slots), "order")


def with_overrides(assignment: Assignment, overrides: Mapping[int, Slot | None]) -> Assignment:
    targets = [slot for slot in overrides.values() if slot is not None]
    if len(targets) != len(set(targets)):
        raise ValueError("two photos cannot be assigned to the same slot")
    count = len(assignment.slots)
    for photo in overrides:
        if not 0 <= photo < count:
            raise ValueError(f"photo index {photo} is out of range")

    slots = list(assignment.slots)
    confidences = list(assignment.confidences)
    for photo, target in overrides.items():
        previous = slots[photo]
        if target is not None:
            # Drag-to-swap: the displaced photo takes the mover's old slot (tray if none).
            holder = next(
                (i for i, slot in enumerate(slots) if slot == target and i != photo), None
            )
            if holder is not None:
                slots[holder] = previous
                confidences[holder] = None
        slots[photo] = target
        confidences[photo] = None
    return Assignment(tuple(slots), tuple(confidences), assignment.method)
