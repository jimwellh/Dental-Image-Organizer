"""Shooting-order placement in which unreadable photos keep their rank (spec §4.6 fallback).

The CLI batch, library composite cases, and teaching sessions all place photos this way.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from dental_image_organizer.core.slots import Slot
from dental_image_organizer.ml.assignment import shooting_rank


@dataclass(frozen=True)
class OrderedPhoto:
    """`key` is the caller's id for the photo, unique and in input order; `name` its file name.

    An unusable photo (one that failed to decode) is a hole: it takes its rank but no slot.
    """

    key: int
    name: str
    taken: datetime | None
    usable: bool = True


def shooting_slots(photos: Sequence[OrderedPhoto], order: Sequence[Slot]) -> dict[int, Slot]:
    """Shooting order in which each unreadable photo still holds its rank, leaving it empty.

    Without the hole, every later photo would move up one slot. Photos may be passed in any
    order, so a hole ranks by the capture time left in its header; without one, it ranks right
    after the photo whose file name precedes it, because cameras number files in shooting
    order. Equal capture times rank by file name for the same reason. A hole without a capture
    time still ranks wrong when the camera's file numbers wrap past 9999 next to it.
    """
    # Listed by file name, so shooting_rank breaks a tie of capture times by file name.
    timed = sorted(
        [(p.taken, p) for p in photos if p.taken is not None],
        key=lambda entry: (_name_key(entry[1].name), entry[1].key),
    )
    ranked = [timed[i][1] for i in shooting_rank([taken for taken, _ in timed])]
    # A usable photo without a capture time (a hand-edited record) ranks as such a hole does.
    untimed = sorted(
        (p for p in photos if p.taken is None), key=lambda p: (_name_key(p.name), p.key)
    )
    for photo in untimed:
        ranked.insert(_name_position(ranked, photo), photo)
    by_key = {photo.key: slot for photo, slot in zip(ranked, order, strict=False)}
    return {p.key: by_key[p.key] for p in photos if p.usable and p.key in by_key}


def _name_position(ranked: Sequence[OrderedPhoto], photo: OrderedPhoto) -> int:
    """The rank right after the photo whose file name precedes `photo`'s."""
    # Input order settles equal names, such as a retake saved in another folder.
    key = (_name_key(photo.name), photo.key)
    keys = [(_name_key(p.name), p.key) for p in ranked]
    earlier = [i for i, other in enumerate(keys) if other < key]
    if earlier:
        return max(earlier, key=lambda i: keys[i]) + 1
    # No file name precedes it, so it was shot before the photo whose name comes first; with
    # nothing ranked yet, it starts the order.
    return min(range(len(keys)), key=lambda i: keys[i], default=0)


def _name_key(name: str) -> tuple[str | int, ...]:
    # Numbers compare as numbers, so IMG_99 sorts before IMG_100; macOS may hand over
    # decomposed (NFD) names.
    folded = unicodedata.normalize("NFC", name).casefold()
    parts = re.split(r"(\d+)", folded)
    return tuple(int(part) if i % 2 else part for i, part in enumerate(parts))
