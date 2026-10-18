"""Accumulated labelled embeddings that train the slot classifier (spec §4.6)."""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

import numpy as np
import numpy.typing as npt

from dental_image_organizer.core.slots import SLOTS, Slot
from dental_image_organizer.ml.embedder import EMBEDDING_DIM, EmbeddingPair

SampleSource = Literal["export", "teach"]
SAMPLES_FILE = "samples.npz"


@dataclass(frozen=True)
class SampleSet:
    pairs: EmbeddingPair
    slots: tuple[Slot, ...]
    case_ids: tuple[str, ...]
    sources: tuple[SampleSource, ...]
    timestamps: npt.NDArray[np.float64]
    model_hashes: tuple[str, ...]

    def for_model(self, model_hash: str) -> SampleSet:
        # Embeddings from another model live in a different space and must never be mixed in.
        keep = [i for i, h in enumerate(self.model_hashes) if h == model_hash]
        return SampleSet(
            self.pairs.take(keep),
            tuple(self.slots[i] for i in keep),
            tuple(self.case_ids[i] for i in keep),
            tuple(self.sources[i] for i in keep),
            self.timestamps[keep],
            tuple(self.model_hashes[i] for i in keep),
        )

    def counts(self) -> dict[Slot, int]:
        counts: dict[Slot, int] = dict.fromkeys(SLOTS, 0)
        for slot in self.slots:
            counts[slot] += 1
        return counts


def empty_samples() -> SampleSet:
    empty = np.empty((0, EMBEDDING_DIM), np.float32)
    return SampleSet(EmbeddingPair(empty, empty), (), (), (), np.empty(0, np.float64), ())


class SampleStore:
    def __init__(self, directory: Path) -> None:
        self.path = directory / SAMPLES_FILE

    def load(self) -> SampleSet:
        if not self.path.exists():
            return empty_samples()
        with np.load(self.path, allow_pickle=False) as data:
            return SampleSet(
                EmbeddingPair(
                    data["original"].astype(np.float32), data["mirrored"].astype(np.float32)
                ),
                cast("tuple[Slot, ...]", tuple(data["slots"].tolist())),
                tuple(data["case_ids"].tolist()),
                cast("tuple[SampleSource, ...]", tuple(data["sources"].tolist())),
                data["timestamps"].astype(np.float64),
                tuple(data["model_hashes"].tolist()),
            )

    def append(
        self,
        pairs: EmbeddingPair,
        slots: Sequence[Slot],
        *,
        case_id: str,
        source: SampleSource,
        model_hash: str,
        timestamp: float,
    ) -> SampleSet:
        old = self.load()
        added = len(slots)
        merged = SampleSet(
            EmbeddingPair(
                np.concatenate([old.pairs.original, pairs.original]),
                np.concatenate([old.pairs.mirrored, pairs.mirrored]),
            ),
            (*old.slots, *slots),
            (*old.case_ids, *([case_id] * added)),
            (*old.sources, *([source] * added)),
            np.concatenate([old.timestamps, np.full(added, timestamp, np.float64)]),
            (*old.model_hashes, *([model_hash] * added)),
        )
        self._write(merged)
        return merged

    def _write(self, samples: SampleSet) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        # An open file object stops numpy from appending ".npz" to the temp name.
        with tmp.open("wb") as handle:
            np.savez_compressed(
                handle,
                original=samples.pairs.original.astype(np.float16),
                mirrored=samples.pairs.mirrored.astype(np.float16),
                slots=np.array(samples.slots, dtype=str),
                case_ids=np.array(samples.case_ids, dtype=str),
                sources=np.array(samples.sources, dtype=str),
                timestamps=samples.timestamps,
                model_hashes=np.array(samples.model_hashes, dtype=str),
            )
        os.replace(tmp, self.path)
