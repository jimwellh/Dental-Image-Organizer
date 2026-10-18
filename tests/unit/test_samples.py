import os
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest

from dental_image_organizer.core.slots import SLOTS
from dental_image_organizer.errors import StorageError
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


def _truncate(path: Path) -> None:
    path.write_bytes(path.read_bytes()[: path.stat().st_size // 2])


def _drop_slots(path: Path) -> None:
    with np.load(path, allow_pickle=False) as data:
        arrays = {name: data[name] for name in data.files if name != "slots"}
    with path.open("wb") as handle:
        np.savez_compressed(handle, **arrays)


def _single_array(path: Path) -> None:
    with path.open("wb") as handle:
        np.save(handle, np.zeros(3))


_DAMAGE: dict[str, Callable[[Path], object]] = {
    "empty": lambda path: path.write_bytes(b""),
    "not a zip": lambda path: path.write_bytes(b"not a sample store" * 10),
    "truncated": _truncate,
    "missing array": _drop_slots,
    "single array": _single_array,
}


@pytest.mark.parametrize("damage", list(_DAMAGE))
def test_a_damaged_store_is_reported_with_its_folder(tmp_path: Path, damage: str) -> None:
    pair, slots = synthetic_pairs(1, 3)
    store = SampleStore(tmp_path)
    store.append(pair, slots, case_id="a", source="teach", model_hash="h", timestamp=1.0)
    _DAMAGE[damage](store.path)

    with pytest.raises(StorageError) as info:
        store.load()

    assert str(info.value) == (
        f"The AI sample store in {tmp_path} is damaged; move that folder aside to start over."
    )


def test_the_store_reaches_the_disk_before_it_replaces_the_old_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    real_fsync, real_replace = os.fsync, os.replace

    def fsync(fd: int) -> None:
        events.append("fsync")
        real_fsync(fd)

    def replace(src: Path, dst: Path) -> None:
        events.append("replace")
        real_replace(src, dst)

    monkeypatch.setattr(os, "fsync", fsync)
    monkeypatch.setattr(os, "replace", replace)
    pair, slots = synthetic_pairs(1, 3)

    SampleStore(tmp_path).append(
        pair, slots, case_id="a", source="teach", model_hash="h", timestamp=1.0
    )

    assert events == ["fsync", "replace"]
