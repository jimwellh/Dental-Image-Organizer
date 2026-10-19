"""Accumulated labelled embeddings that train the slot classifier (spec §4.6)."""

from __future__ import annotations

import os
import zlib
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast
from zipfile import BadZipFile

import numpy as np
import numpy.typing as npt
import structlog

from dental_image_organizer.core.slots import SLOTS, Slot
from dental_image_organizer.errors import StorageError
from dental_image_organizer.ml.embedder import EMBEDDING_DIM, EmbeddingPair

SampleSource = Literal["export", "teach"]
SAMPLES_FILE = "samples.npz"

_log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class SampleSet:
    """`photo_ids` are SHA-256 hashes of the photo files: they identify a photo without its name."""

    pairs: EmbeddingPair
    slots: tuple[Slot, ...]
    photo_ids: tuple[str, ...]
    case_ids: tuple[str, ...]
    sources: tuple[SampleSource, ...]
    timestamps: npt.NDArray[np.float64]
    model_hashes: tuple[str, ...]

    def for_model(self, model_hash: str) -> SampleSet:
        # Embeddings from another model live in a different space and must never be mixed in.
        return self.take([i for i, h in enumerate(self.model_hashes) if h == model_hash])

    def take(self, keep: Sequence[int]) -> SampleSet:
        return SampleSet(
            self.pairs.take(keep),
            tuple(self.slots[i] for i in keep),
            tuple(self.photo_ids[i] for i in keep),
            tuple(self.case_ids[i] for i in keep),
            tuple(self.sources[i] for i in keep),
            self.timestamps[np.asarray(keep, dtype=np.intp)],
            tuple(self.model_hashes[i] for i in keep),
        )

    def counts(self) -> dict[Slot, int]:
        counts: dict[Slot, int] = dict.fromkeys(SLOTS, 0)
        for slot in self.slots:
            counts[slot] += 1
        return counts


def empty_samples() -> SampleSet:
    empty = np.empty((0, EMBEDDING_DIM), np.float32)
    return SampleSet(EmbeddingPair(empty, empty), (), (), (), (), np.empty(0, np.float64), ())


class SampleStore:
    def __init__(self, directory: Path) -> None:
        self.path = directory / SAMPLES_FILE

    def load(self) -> SampleSet:
        """The stored samples; raises StorageError when the file cannot be read back."""
        if not self.path.exists():
            return empty_samples()
        try:
            # A .npy file in its place loads as a bare array, which is no context manager:
            # TypeError. A corrupt compressed member raises zlib.error.
            with np.load(self.path, allow_pickle=False) as data:
                samples = SampleSet(
                    EmbeddingPair(
                        data["original"].astype(np.float32), data["mirrored"].astype(np.float32)
                    ),
                    cast("tuple[Slot, ...]", tuple(data["slots"].tolist())),
                    tuple(data["photo_ids"].tolist()),
                    tuple(data["case_ids"].tolist()),
                    cast("tuple[SampleSource, ...]", tuple(data["sources"].tolist())),
                    data["timestamps"].astype(np.float64),
                    tuple(data["model_hashes"].tolist()),
                )
            _check_consistent(samples)
        except (BadZipFile, EOFError, KeyError, OSError, TypeError, ValueError, zlib.error) as exc:
            _log.error("sample_store_damaged", error=type(exc).__name__)
            raise StorageError(
                f"The AI sample store in {self.path.parent} is damaged; "
                "move that folder aside to start over."
            ) from exc
        return samples

    def append(
        self,
        pairs: EmbeddingPair,
        slots: Sequence[Slot],
        *,
        photo_ids: Sequence[str],
        case_id: str,
        source: SampleSource,
        model_hash: str,
        timestamp: float,
    ) -> SampleSet:
        """Add confirmed samples; a photo already stored for this model gets its new label."""
        if not len(pairs) == len(slots) == len(photo_ids):
            raise ValueError(
                f"got {len(pairs)} embeddings for {len(slots)} labels and "
                f"{len(photo_ids)} photo ids"
            )
        repeated = sorted(photo for photo, count in Counter(photo_ids).items() if count > 1)
        if repeated:
            raise ValueError(f"photo ids {repeated} appear more than once; one photo has one label")
        stored = self.load()
        # Re-confirming a corrected set must replace its earlier labels, not contradict them,
        # and must not count the same photos twice toward the trained state.
        relabeled = set(photo_ids)
        old = stored.take(
            [
                i
                for i, (photo, model) in enumerate(
                    zip(stored.photo_ids, stored.model_hashes, strict=True)
                )
                if model != model_hash or photo not in relabeled
            ]
        )
        added = len(slots)
        merged = SampleSet(
            EmbeddingPair(
                np.concatenate([old.pairs.original, pairs.original]),
                np.concatenate([old.pairs.mirrored, pairs.mirrored]),
            ),
            (*old.slots, *slots),
            (*old.photo_ids, *photo_ids),
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
                photo_ids=np.array(samples.photo_ids, dtype=str),
                case_ids=np.array(samples.case_ids, dtype=str),
                sources=np.array(samples.sources, dtype=str),
                timestamps=samples.timestamps,
                model_hashes=np.array(samples.model_hashes, dtype=str),
            )
            # Without the fsync, a power loss soon after the rename can leave an empty file.
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, self.path)


def _check_consistent(samples: SampleSet) -> None:
    # Arrays that parse but disagree would otherwise fail later, while training.
    count = len(samples.slots)
    lengths = {
        len(samples.pairs.original),
        len(samples.pairs.mirrored),
        count,
        len(samples.photo_ids),
        len(samples.case_ids),
        len(samples.sources),
        len(samples.timestamps),
        len(samples.model_hashes),
    }
    if lengths != {count}:
        raise ValueError("the sample arrays differ in length")
    if samples.pairs.original.shape[1:] != (EMBEDDING_DIM,) or (
        samples.pairs.mirrored.shape[1:] != (EMBEDDING_DIM,)
    ):
        raise ValueError("the sample embeddings do not fit the embedding model")
    if not set(samples.slots) <= set(SLOTS):
        raise ValueError("a sample names an unknown slot")
