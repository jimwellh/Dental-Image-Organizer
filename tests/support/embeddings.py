"""Synthetic embeddings with a known group and left/right structure."""

from __future__ import annotations

import numpy as np

from dental_image_organizer.core.slots import GROUPS, SLOTS, Slot, slot_group, slot_side
from dental_image_organizer.ml.embedder import EMBEDDING_DIM, EmbeddingPair, normalize_blocks

_NOISE = 0.3


def synthetic_pairs(per_slot: int, seed: int) -> tuple[EmbeddingPair, list[Slot]]:
    """`per_slot` samples of every slot: SLOTS order, repeated `per_slot` times.

    Group centers and the side axis are fixed (seed 100), so train and test sets built with
    different `seed` values share one geometry and differ only in noise.
    """
    structure = np.random.default_rng(100)
    centers = structure.normal(size=(len(GROUPS), EMBEDDING_DIM))
    axis = structure.normal(size=EMBEDDING_DIM)
    noise = np.random.default_rng(seed)

    slots: list[Slot] = [slot for _ in range(per_slot) for slot in SLOTS]
    base = np.stack(
        [
            centers[GROUPS.index(slot_group(s))] + _NOISE * noise.normal(size=EMBEDDING_DIM)
            for s in slots
        ]
    )
    sides = {"right": 1.0, "left": -1.0, None: 0.0}
    sign = np.array([sides[slot_side(s)] for s in slots])[:, None]
    pair = EmbeddingPair(normalize_blocks(base + sign * axis), normalize_blocks(base - sign * axis))
    return pair, slots
