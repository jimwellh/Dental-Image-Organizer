from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from dental_image_organizer.core.slots import SLOTS
from dental_image_organizer.ml.assignment import (
    assign_by_classifier,
    assign_by_order,
    with_overrides,
)

NINE = [datetime(2026, 1, 1, 9, 0, s) for s in range(9)]


def test_assignment_is_globally_optimal_not_greedy() -> None:
    probs = np.full((9, 9), 0.01)
    np.fill_diagonal(probs, 0.6)
    probs[0, 0], probs[0, 1], probs[1, 0], probs[1, 1] = 0.9, 0.85, 0.8, 0.1

    assert assign_by_classifier(probs).slots[:2] == (SLOTS[1], SLOTS[0])  # greedy gives 0 -> 0


def test_extra_photos_go_to_the_tray_and_low_confidence_is_flagged() -> None:  # Review Focus 4
    probs = np.full((10, 9), 0.01)
    probs[:9, :9] += np.eye(9) * 0.9
    probs[4, 4] = 0.3

    assignment = assign_by_classifier(probs)

    assert assignment.slots[9] is None
    assert assignment.low_confidence == frozenset({SLOTS[4]})


def test_order_fallback_sorts_by_time_and_fills_the_order() -> None:
    t0 = datetime(2026, 10, 16, 9, 0)
    times = [t0 + timedelta(seconds=s) for s in (30, 10, 20)]
    times[0] = times[0].replace(tzinfo=timezone(timedelta(hours=8)))  # mixed aware and naive

    assignment = assign_by_order(times, SLOTS)

    assert assignment.slots == (SLOTS[2], SLOTS[0], SLOTS[1])
    assert assignment.method == "order"


def test_order_must_list_every_slot_once() -> None:
    with pytest.raises(ValueError):
        assign_by_order(NINE[:1], SLOTS[:8] + SLOTS[:1])


def test_override_swaps_like_a_drag() -> None:
    base = assign_by_order([*NINE, datetime(2026, 1, 1, 9, 0, 30)], SLOTS)

    moved = with_overrides(base, {0: "upper_left"})
    from_tray = with_overrides(base, {9: "occlusion_front", 1: None})

    assert moved.slots[:3] == ("upper_left", "upper_occlusal", "upper_right")
    assert from_tray.slots[9] == "occlusion_front"
    assert from_tray.slots[4] is None and from_tray.slots[1] is None


def test_two_overrides_to_one_slot_are_rejected() -> None:
    with pytest.raises(ValueError):
        with_overrides(assign_by_order(NINE, SLOTS), {0: "upper_left", 1: "upper_left"})
