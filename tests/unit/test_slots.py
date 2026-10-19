import pytest

from dental_image_organizer.core.slots import (
    DEFAULT_FLIPS,
    SLOTS,
    parse_slot,
    slot_for,
    slot_group,
    slot_side,
)


def test_slots_are_row_major() -> None:
    assert SLOTS == (
        "upper_right", "upper_occlusal", "upper_left",
        "occlusion_right", "occlusion_front", "occlusion_left",
        "lower_right", "lower_occlusal", "lower_left",
    )  # fmt: skip


def test_group_and_side_round_trip() -> None:
    for slot in SLOTS:
        assert slot_for(slot_group(slot), slot_side(slot)) == slot
    assert slot_group("occlusion_left") == "occlusion_side"
    assert slot_side("upper_occlusal") is None


def test_default_flips_follow_the_spec_starting_values() -> None:
    assert DEFAULT_FLIPS["upper_occlusal"] == DEFAULT_FLIPS["lower_occlusal"] == "vertical"
    corners = ("upper_right", "upper_left", "lower_right", "lower_left")
    assert {DEFAULT_FLIPS[s] for s in corners} == {"horizontal"}
    assert {DEFAULT_FLIPS[s] for s in SLOTS if s.startswith("occlusion")} == {"none"}


def test_unknown_slot_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown slot"):
        parse_slot("upper_middle")
