"""Flip learning (spec §4.7.2, amendment 5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from dental_image_organizer.config import Settings
from dental_image_organizer.core.slots import Flip, Slot
from dental_image_organizer.errors import StorageError
from dental_image_organizer.services.flips import (
    FlipConfirmation,
    FlipStore,
    effective_flips,
    flip_status,
    learned_flips,
)

SLOT: Slot = "upper_right"


def _history(*flips: Flip) -> list[FlipConfirmation]:
    return [FlipConfirmation(f"c{i}", SLOT, flip, float(i)) for i, flip in enumerate(flips)]


def test_default_changes_only_after_three_confirmations() -> None:
    start = Settings().flip_start | {SLOT: "horizontal"}
    assert SLOT not in learned_flips(_history("none", "none"), start)
    assert learned_flips(_history("none", "none", "none"), start)[SLOT] == "none"


def test_learned_value_follows_the_last_five() -> None:
    start = Settings().flip_start
    history = _history("none", "none", "none", "vertical", "vertical", "vertical")
    assert learned_flips(history, start)[SLOT] == "vertical"


def test_a_tie_keeps_the_current_default() -> None:
    start = Settings().flip_start | {SLOT: "horizontal"}
    history = _history("none", "vertical", "horizontal")
    assert learned_flips(history, start)[SLOT] == "horizontal"
    history = _history("none", "vertical", "horizontal", "none")
    assert learned_flips(history, start)[SLOT] == "none"
    history = _history("none", "vertical", "horizontal", "none", "vertical")
    assert learned_flips(history, start)[SLOT] == "none"


def test_a_pinned_slot_never_changes_and_records_nothing(tmp_path: Path) -> None:
    store = FlipStore(tmp_path / "flips.json")
    for i in range(5):
        store.record(f"c{i}", {SLOT: "none"}, pins={SLOT: "vertical"}, timestamp=float(i))
    settings = Settings(flip_pins={SLOT: "vertical"})
    assert effective_flips(settings, store.load())[SLOT] == "vertical"
    assert all(c.slot != SLOT for c in store.load())


def test_recording_a_case_again_replaces_it(tmp_path: Path) -> None:
    store = FlipStore(tmp_path / "flips.json")
    for i in range(3):
        store.record("c1", {SLOT: "none", "lower_left": "vertical"}, pins={}, timestamp=float(i))
    history = store.load()
    assert len(history) == 2
    assert {c.slot for c in history} == {SLOT, "lower_left"}


def test_precedence_is_pin_then_learned_then_start(tmp_path: Path) -> None:
    store = FlipStore(tmp_path / "flips.json")
    for i in range(3):
        store.record(f"c{i}", {SLOT: "none"}, pins={}, timestamp=float(i))
    history = store.load()
    base = Settings().flip_start | {SLOT: "horizontal"}
    pinned = Settings(flip_start=base, flip_pins={SLOT: "vertical"})
    free = Settings(flip_start=base)
    assert effective_flips(pinned, history)[SLOT] == "vertical"
    assert effective_flips(free, history)[SLOT] == "none"
    assert effective_flips(free, history)["lower_left"] == free.flip_start["lower_left"]

    status = flip_status(pinned, history)[SLOT]
    assert (status.effective, status.start, status.learned) == ("vertical", "horizontal", "none")
    assert status.pinned
    assert status.confirmations == 3
    assert flip_status(free, history)["lower_left"].learned is None


def test_damaged_flip_history_is_a_storage_error(tmp_path: Path) -> None:
    path = tmp_path / "flips.json"
    path.write_bytes(b"{")
    with pytest.raises(StorageError):
        FlipStore(path).load()
