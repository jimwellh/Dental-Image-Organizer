"""Shooting-order placement in which unreadable photos keep their rank (spec §4.6)."""

from datetime import datetime, timedelta

from dental_image_organizer.core.slots import SLOTS
from dental_image_organizer.services.shooting_order import OrderedPhoto, shooting_slots

START = datetime(2026, 10, 16, 10)


def _at(seconds: int) -> datetime:
    return START + timedelta(seconds=seconds)


def test_photos_take_slots_in_capture_order() -> None:
    # Passed in any order, as Finder hands over a selection in the order it was clicked.
    photos = [
        OrderedPhoto(0, "IMG_0003.jpg", _at(30)),
        OrderedPhoto(1, "IMG_0001.jpg", _at(10)),
        OrderedPhoto(2, "IMG_0002.jpg", _at(20)),
    ]
    order = tuple(reversed(SLOTS))

    assert shooting_slots(photos, SLOTS) == {1: SLOTS[0], 2: SLOTS[1], 0: SLOTS[2]}
    assert shooting_slots(photos, order) == {1: order[0], 2: order[1], 0: order[2]}


def test_a_hole_keeps_its_rank() -> None:
    photos = [
        OrderedPhoto(0, "IMG_0001.jpg", _at(10)),
        # A name that sorts last: only the capture time left in its header ranks it second.
        OrderedPhoto(1, "IMG_0100.jpg", _at(20), usable=False),
        OrderedPhoto(2, "IMG_0003.jpg", _at(30)),
        # No capture time: it ranks right after IMG_0003, whose name precedes it.
        OrderedPhoto(3, "IMG_0004.jpg", None, usable=False),
        OrderedPhoto(4, "IMG_0005.jpg", _at(50)),
    ]

    assert shooting_slots(photos, SLOTS) == {0: SLOTS[0], 2: SLOTS[2], 4: SLOTS[4]}


def test_equal_times_rank_by_file_number() -> None:
    photos = [OrderedPhoto(0, "IMG_100.jpg", _at(10)), OrderedPhoto(1, "IMG_99.jpg", _at(10))]

    assert shooting_slots(photos, SLOTS) == {1: SLOTS[0], 0: SLOTS[1]}
