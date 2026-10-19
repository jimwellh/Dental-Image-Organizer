"""The 3 x 3 composite slots, their classifier groups, and flip defaults (spec §4.6, §11)."""

from typing import Literal, get_args

Slot = Literal[
    "upper_right", "upper_occlusal", "upper_left",
    "occlusion_right", "occlusion_front", "occlusion_left",
    "lower_right", "lower_occlusal", "lower_left",
]  # fmt: skip
SlotGroup = Literal[
    "upper_buccal", "upper_occlusal", "occlusion_side",
    "occlusion_front", "lower_buccal", "lower_occlusal",
]  # fmt: skip
Side = Literal["right", "left"]
Flip = Literal["none", "horizontal", "vertical", "rotate_180"]

# Row-major grid order; derived from the Literal so the type and the runtime tuple cannot drift.
SLOTS: tuple[Slot, ...] = get_args(Slot)
GROUPS: tuple[SlotGroup, ...] = get_args(SlotGroup)
FLIPS: tuple[Flip, ...] = get_args(Flip)

_GROUP: dict[Slot, SlotGroup] = {
    "upper_right": "upper_buccal",
    "upper_left": "upper_buccal",
    "upper_occlusal": "upper_occlusal",
    "occlusion_right": "occlusion_side",
    "occlusion_left": "occlusion_side",
    "occlusion_front": "occlusion_front",
    "lower_right": "lower_buccal",
    "lower_left": "lower_buccal",
    "lower_occlusal": "lower_occlusal",
}
_SIDE: dict[Slot, Side | None] = {
    slot: ("right" if slot.endswith("_right") else "left" if slot.endswith("_left") else None)
    for slot in SLOTS
}
_SLOT_FOR: dict[tuple[SlotGroup, Side | None], Slot] = {
    (_GROUP[slot], _SIDE[slot]): slot for slot in SLOTS
}

# Starting values only: they are tuned on real photos (spec §11).
DEFAULT_FLIPS: dict[Slot, Flip] = {
    slot: (
        "vertical"
        if slot.endswith("_occlusal")
        else "none"
        if slot.startswith("occlusion")
        else "horizontal"
    )
    for slot in SLOTS
}


def slot_group(slot: Slot) -> SlotGroup:
    return _GROUP[slot]


def slot_side(slot: Slot) -> Side | None:
    return _SIDE[slot]


def slot_for(group: SlotGroup, side: Side | None) -> Slot:
    return _SLOT_FOR[(group, side)]


def parse_slot(text: str) -> Slot:
    if text in SLOTS:
        return text
    raise ValueError(f"unknown slot {text!r}; expected one of {', '.join(SLOTS)}")
