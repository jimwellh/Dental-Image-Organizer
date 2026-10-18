from pathlib import Path

import numpy as np

from dental_image_organizer.core.slots import SLOTS
from dental_image_organizer.ml.samples import SampleStore
from support.embeddings import synthetic_pairs


def test_samples_accumulate_and_filter_by_model(tmp_path: Path) -> None:
    pair, slots = synthetic_pairs(1, 3)
    store = SampleStore(tmp_path)

    store.append(pair, slots, case_id="a", source="teach", model_hash="h1", timestamp=1.0)
    merged = store.append(pair, slots, case_id="b", source="export", model_hash="h2", timestamp=2.0)

    assert len(merged.slots) == 18
    assert store.load().for_model("h1").counts() == dict.fromkeys(SLOTS, 1)
    np.testing.assert_allclose(store.load().pairs.original[:9], pair.original, atol=2e-3)  # fp16
    assert [p.name for p in tmp_path.iterdir()] == ["samples.npz"]  # no temp file left


def test_missing_store_loads_empty(tmp_path: Path) -> None:
    assert SampleStore(tmp_path).load().counts() == dict.fromkeys(SLOTS, 0)
