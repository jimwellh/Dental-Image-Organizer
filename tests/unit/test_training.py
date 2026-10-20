"""Learning trains on the appended samples (spec §4.6)."""

from __future__ import annotations

from pathlib import Path

import pytest

from dental_image_organizer.ml import training
from dental_image_organizer.ml.samples import SampleStore
from support.embeddings import synthetic_pairs


def test_learn_loads_the_store_only_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    real = SampleStore.load

    def counting(self: SampleStore):
        calls.append(1)
        return real(self)

    monkeypatch.setattr(SampleStore, "load", counting)
    pairs, slots = synthetic_pairs(3, 1)
    ids = [f"p{i}" for i in range(len(slots))]
    assert training.learn(
        tmp_path,
        pairs,
        slots,
        photo_ids=ids,
        case_id="c",
        source="export",
        model_hash="m",
        timestamp=0.0,
    )
    assert len(calls) == 1
