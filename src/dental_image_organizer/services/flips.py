"""Per-slot mirror flips learned from confirmed exports (spec §4.7.2 step 1)."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast, get_args

from dental_image_organizer.config import Settings
from dental_image_organizer.core.slots import SLOTS, Flip, Slot
from dental_image_organizer.errors import StorageError
from dental_image_organizer.storage.atomic import write_atomic

FLIPS_FILE = "flips.json"
MIN_CONFIRMATIONS = 3
RECENT_CONFIRMATIONS = 5
_SCHEMA_VERSION = 1
_DAMAGED = (
    "The flip history is damaged; move flips.json out of the classifier folder to start over."
)
_FLIPS: tuple[str, ...] = get_args(Flip)


@dataclass(frozen=True)
class FlipConfirmation:
    case_id: str
    slot: Slot
    flip: Flip
    timestamp: float


@dataclass(frozen=True)
class FlipStatus:
    effective: Flip
    start: Flip
    learned: Flip | None
    pinned: bool
    confirmations: int


class FlipStore:
    """Not thread-safe: callers that share the folder serialize `record` (see AiRuntime)."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> tuple[FlipConfirmation, ...]:
        if not self.path.exists():
            return ()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data["schema_version"] != _SCHEMA_VERSION:
                raise ValueError("unknown schema version")
            entries = []
            for item in data["confirmations"]:
                if item["slot"] not in SLOTS or item["flip"] not in _FLIPS:
                    raise ValueError("unknown slot or flip")
                entries.append(
                    FlipConfirmation(
                        str(item["case_id"]),
                        cast("Slot", item["slot"]),
                        cast("Flip", item["flip"]),
                        float(item["timestamp"]),
                    )
                )
        except (OSError, KeyError, TypeError, ValueError) as exc:  # JSONDecodeError is a ValueError
            raise StorageError(_DAMAGED) from exc
        return tuple(entries)

    def record(
        self,
        case_id: str,
        flips: Mapping[Slot, Flip],
        *,
        pins: Mapping[Slot, Flip],
        timestamp: float,
    ) -> tuple[FlipConfirmation, ...]:
        """Replace the case's confirmations; a pinned slot records nothing."""
        kept = [c for c in self.load() if c.case_id != case_id]
        kept.extend(
            FlipConfirmation(case_id, slot, flip, timestamp)
            for slot, flip in flips.items()
            if slot not in pins
        )
        history = tuple(kept)
        payload = {
            "schema_version": _SCHEMA_VERSION,
            "confirmations": [
                {"case_id": c.case_id, "slot": c.slot, "flip": c.flip, "timestamp": c.timestamp}
                for c in history
            ],
        }
        write_atomic(self.path, json.dumps(payload).encode("utf-8"))
        return history


def learned_flips(
    history: Sequence[FlipConfirmation], start: Mapping[Slot, Flip]
) -> dict[Slot, Flip]:
    """Replay the history per slot; only slots with enough confirmations get a learned value."""
    learned: dict[Slot, Flip] = {}
    for slot in SLOTS:
        current, seen = start[slot], []
        for confirmation in (c for c in history if c.slot == slot):
            seen.append(confirmation.flip)
            if len(seen) < MIN_CONFIRMATIONS:
                continue
            counts = Counter(seen[-RECENT_CONFIRMATIONS:])
            top = max(counts.values())
            winners = [flip for flip, n in counts.items() if n == top]
            if len(winners) == 1:  # a tie keeps the current default (spec §4.7.2)
                current = winners[0]
        if len(seen) >= MIN_CONFIRMATIONS:
            learned[slot] = current
    return learned


def effective_flips(settings: Settings, history: Sequence[FlipConfirmation]) -> dict[Slot, Flip]:
    """Pin, else learned, else the configured start value."""
    learned = learned_flips(history, settings.flip_start)
    return {
        slot: settings.flip_pins.get(slot) or learned.get(slot) or settings.flip_start[slot]
        for slot in SLOTS
    }


def flip_status(settings: Settings, history: Sequence[FlipConfirmation]) -> dict[Slot, FlipStatus]:
    learned = learned_flips(history, settings.flip_start)
    effective = effective_flips(settings, history)
    return {
        slot: FlipStatus(
            effective[slot],
            settings.flip_start[slot],
            learned.get(slot),
            slot in settings.flip_pins,
            sum(1 for c in history if c.slot == slot),
        )
        for slot in SLOTS
    }
