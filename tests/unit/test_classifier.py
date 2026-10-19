import json
import os
from pathlib import Path

import numpy as np
import pytest

from dental_image_organizer.core.slots import SLOTS
from dental_image_organizer.errors import StorageError
from dental_image_organizer.ml.classifier import load_classifier, train_classifier
from dental_image_organizer.ml.embedder import EmbeddingPair
from support.embeddings import synthetic_pairs


def _trained():
    train, slots = synthetic_pairs(3, 1)
    classifier = train_classifier(train, slots, "h")
    assert classifier is not None
    return classifier, train, slots


def test_untrained_until_every_slot_has_three_samples() -> None:
    pair, slots = synthetic_pairs(3, 1)

    assert train_classifier(pair.take(range(1, len(slots))), slots[1:], "h") is None
    assert train_classifier(pair, slots, "h") is not None


def test_embeddings_and_labels_must_match_in_number() -> None:
    pair, slots = synthetic_pairs(3, 1)

    with pytest.raises(ValueError, match="27 embeddings for 26 labels"):
        train_classifier(pair, slots[:-1], "h")


def test_three_samples_per_slot_classify_new_photos() -> None:
    classifier, _, _ = _trained()
    test, test_slots = synthetic_pairs(5, 2)

    predicted = [SLOTS[i] for i in classifier.slot_probabilities(test).argmax(axis=1)]

    assert predicted == test_slots


def test_side_score_is_exactly_antisymmetric_under_mirroring() -> None:  # spec §8.1
    classifier, train, _ = _trained()

    swapped = EmbeddingPair(train.mirrored, train.original)

    np.testing.assert_allclose(classifier.side_scores(swapped), -classifier.side_scores(train))


def test_mirror_images_land_in_the_same_group_on_opposite_sides() -> None:  # spec §8.1
    classifier, train, slots = _trained()
    right = train.take([slots.index("upper_right")])

    as_is = SLOTS[int(classifier.slot_probabilities(right).argmax())]
    flipped = EmbeddingPair(right.mirrored, right.original)
    mirrored = SLOTS[int(classifier.slot_probabilities(flipped).argmax())]

    assert (as_is, mirrored) == ("upper_right", "upper_left")


def test_classifier_round_trips_and_ignores_another_model(tmp_path: Path) -> None:  # §8.1
    classifier, train, _ = _trained()
    classifier.save(tmp_path / "model.json")

    loaded = load_classifier(tmp_path / "model.json", "h")

    assert loaded is not None
    np.testing.assert_allclose(
        loaded.slot_probabilities(train), classifier.slot_probabilities(train)
    )
    assert load_classifier(tmp_path / "model.json", "other-model") is None
    assert load_classifier(tmp_path / "missing.json", "h") is None


def test_a_file_without_a_model_hash_belongs_to_another_model(tmp_path: Path) -> None:
    (tmp_path / "model.json").write_text(json.dumps({}), encoding="utf-8")

    assert load_classifier(tmp_path / "model.json", "h") is None


@pytest.mark.parametrize(
    "text",
    ["", '{"model_hash": "h", "group', "[]", '{"model_hash": "h"}'],
    ids=["empty", "truncated", "not an object", "missing keys"],
)
def test_a_damaged_file_is_reported_as_damaged(tmp_path: Path, text: str) -> None:
    (tmp_path / "model.json").write_text(text, encoding="utf-8")

    with pytest.raises(StorageError):
        load_classifier(tmp_path / "model.json", "h")


def test_a_file_with_inconsistent_arrays_is_damaged(tmp_path: Path) -> None:
    # It parses and has every key, so without a check it would fail later, mid-assignment.
    classifier, _, _ = _trained()
    classifier.save(tmp_path / "model.json")
    payload = json.loads((tmp_path / "model.json").read_text(encoding="utf-8"))
    payload["side_coef"] = payload["side_coef"][:10]
    (tmp_path / "model.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(StorageError):
        load_classifier(tmp_path / "model.json", "h")


def test_the_file_reaches_the_disk_before_it_replaces_the_old_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    classifier, _, _ = _trained()
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

    classifier.save(tmp_path / "model.json")

    assert events == ["fsync", "replace"]
