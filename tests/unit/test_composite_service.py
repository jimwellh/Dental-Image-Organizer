from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pytest

from dental_image_organizer.core.geometry import flip_pixels
from dental_image_organizer.core.slots import DEFAULT_FLIPS, GROUPS, SLOTS
from dental_image_organizer.ml.assignment import assign_by_order
from dental_image_organizer.ml.classifier import SlotClassifier
from dental_image_organizer.ml.embedder import EMBEDDING_DIM, EmbeddingPair
from dental_image_organizer.services import composite
from dental_image_organizer.services.composite import (
    assign_photos,
    compose,
    plan_composite,
    render_cell,
)
from support.synthetic import grade
from support.views import make_view

TIMES = [datetime(2026, 10, 16, 9) + timedelta(seconds=10 * i) for i in range(10)]


@pytest.fixture(scope="module")
def raw_set() -> list[np.ndarray]:
    """Nine photos as the camera stored them: mirror shots flipped, colors varying."""
    rng = np.random.default_rng(5)
    photos = []
    for i, slot in enumerate(SLOTS):
        view = make_view(slot, 50 + i, angle=float(rng.uniform(-8, 8)))
        view = grade(view, gain=float(rng.uniform(0.85, 1.1)), warm=float(rng.uniform(-8, 8)))
        photos.append(flip_pixels(view, DEFAULT_FLIPS[slot]))
    return photos


def test_plan_places_levels_and_colors_every_slot(raw_set: list[np.ndarray]) -> None:
    plan = plan_composite(raw_set, assign_by_order(TIMES[:9], SLOTS), flips=DEFAULT_FLIPS)

    assert list(plan.cells) == list(SLOTS)
    assert plan.baseline == "occlusion_front" and plan.cells["occlusion_front"].color is None
    assert all(plan.cells[s].color is not None for s in SLOTS if s != "occlusion_front")
    assert all(plan.cells[s].geometry.level.confident for s in SLOTS)
    assert plan.method == "order" and plan.tray == ()


def test_missing_baseline_falls_back_to_the_first_filled_slot(raw_set: list[np.ndarray]) -> None:
    without_front = raw_set[:4] + raw_set[5:]
    order = SLOTS[:4] + SLOTS[5:] + SLOTS[4:5]

    plan = plan_composite(without_front, assign_by_order(TIMES[:8], order), flips=DEFAULT_FLIPS)

    assert plan.baseline == "upper_right" and "occlusion_front" not in plan.cells


def test_positions_must_label_every_image(raw_set: list[np.ndarray]) -> None:
    with pytest.raises(ValueError, match="1 positions for 9 images"):
        plan_composite(
            raw_set, assign_by_order(TIMES[:9], SLOTS), flips=DEFAULT_FLIPS, positions=[1]
        )


def test_a_tenth_photo_goes_to_the_tray(raw_set: list[np.ndarray]) -> None:
    plan = plan_composite(raw_set + raw_set[:1], assign_by_order(TIMES, SLOTS), flips=DEFAULT_FLIPS)

    assert plan.tray == (9,)


def test_a_photo_that_fails_planning_leaves_only_its_slot_empty(
    raw_set: list[np.ndarray], monkeypatch: pytest.MonkeyPatch
) -> None:
    real_normalize_view = composite.normalize_view

    def normalize_failing_on_upper_left(image: np.ndarray, slot: str, *rest: Any) -> Any:
        if slot == "upper_left":
            raise ValueError("synthetic leveling failure")
        return real_normalize_view(image, slot, *rest)

    monkeypatch.setattr(composite, "normalize_view", normalize_failing_on_upper_left)

    plan = plan_composite(raw_set, assign_by_order(TIMES[:9], SLOTS), flips=DEFAULT_FLIPS)

    assert list(plan.cells) == [s for s in SLOTS if s != "upper_left"]
    assert list(plan.failed) == [2] and isinstance(plan.failed[2], ValueError)
    assert plan.baseline == "occlusion_front"


def test_a_failed_baseline_falls_back_to_the_first_surviving_slot(
    raw_set: list[np.ndarray], monkeypatch: pytest.MonkeyPatch
) -> None:
    real_render = composite.render
    baseline_photo = raw_set[SLOTS.index("occlusion_front")]

    def render_failing_on_the_baseline(image: np.ndarray, *args: Any, **kwargs: Any) -> Any:
        if image is baseline_photo:
            raise MemoryError  # the color preview is the first render in planning
        return real_render(image, *args, **kwargs)

    monkeypatch.setattr(composite, "render", render_failing_on_the_baseline)

    plan = plan_composite(raw_set, assign_by_order(TIMES[:9], SLOTS), flips=DEFAULT_FLIPS)

    assert "occlusion_front" not in plan.cells and list(plan.failed) == [4]
    assert plan.baseline == "upper_right" and plan.cells["upper_right"].color is None
    assert all(plan.cells[s].color is not None for s in plan.cells if s != "upper_right")


def test_a_failed_color_fit_fails_only_that_photo(
    raw_set: list[np.ndarray], monkeypatch: pytest.MonkeyPatch
) -> None:
    real_fit = composite.fit_reinhard
    calls = 0

    def fit_failing_once(*args: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 1:  # cells are fitted in grid order, so this is upper_right
            raise ValueError("synthetic fit failure")
        return real_fit(*args)

    monkeypatch.setattr(composite, "fit_reinhard", fit_failing_once)

    plan = plan_composite(raw_set, assign_by_order(TIMES[:9], SLOTS), flips=DEFAULT_FLIPS)

    assert list(plan.cells) == list(SLOTS[1:]) and list(plan.failed) == [0]
    assert plan.baseline == "occlusion_front"


def test_composite_is_three_by_three_cells(raw_set: list[np.ndarray]) -> None:
    plan = plan_composite(raw_set, assign_by_order(TIMES[:9], SLOTS), flips=DEFAULT_FLIPS)

    grid = compose(
        {s: render_cell(raw_set[c.photo], c, (1200, 800)) for s, c in plan.cells.items()}
    )

    assert grid.shape == (2400, 3600, 3)


def test_classifier_is_used_only_with_embeddings() -> None:
    d = EMBEDDING_DIM
    classifier = SlotClassifier("h", GROUPS, np.zeros((6, d)), np.zeros(6), np.zeros(d))
    pair = EmbeddingPair(np.zeros((1, d), np.float32), np.zeros((1, d), np.float32))

    assert assign_photos(TIMES[:1], SLOTS, classifier=classifier).method == "order"
    assert (
        assign_photos(TIMES[:1], SLOTS, classifier=classifier, embeddings=pair).method
        == "classifier"
    )
