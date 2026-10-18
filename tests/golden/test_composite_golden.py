"""Composite leveling and slot-classifier metrics on real photos (spec §4.6, §4.7). Local only.

Layout: local_fixtures/composite/<set>/ holds one session's photos, taken in row-major slot
order. An optional order.txt lists one slot per line when the shooting order differs.
Each run writes reports under local_fixtures/reports/ (gitignored, patient data stays local).
"""

import csv
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import numpy.typing as npt
import pytest

from dental_image_organizer import paths
from dental_image_organizer.core.imageio import RGBImage, decode, resize_long_edge
from dental_image_organizer.core.slots import DEFAULT_FLIPS, SLOTS, Slot, parse_slot
from dental_image_organizer.errors import UnsupportedFormatError
from dental_image_organizer.ml.assignment import Assignment, assign_by_classifier, assign_by_order
from dental_image_organizer.ml.classifier import train_classifier
from dental_image_organizer.ml.embedder import Embedder, EmbeddingPair
from dental_image_organizer.ml.model_store import DEFAULT_MODEL, cached_model
from dental_image_organizer.services.batch_io import capture_time
from dental_image_organizer.services.composite import plan_composite

ROOT = Path(__file__).resolve().parents[2] / "local_fixtures"
SETS = ROOT / "composite"
REPORTS = ROOT / "reports"
REGULARIZATIONS = (1.0, 10.0, 100.0)
MIN_SETS = 4
# Same working size as the headless batch, so the metrics match what `dio composite` computes.
WORKING_LONG_EDGE = 1600

pytestmark = [
    pytest.mark.golden,
    pytest.mark.skipif(not SETS.is_dir(), reason="local_fixtures/composite is not present"),
]


def _sets() -> list[Path]:
    return sorted(p for p in SETS.iterdir() if p.is_dir()) if SETS.is_dir() else []


def _order(folder: Path) -> list[Slot]:
    listing = folder / "order.txt"
    if not listing.is_file():
        return list(SLOTS)
    lines = listing.read_text(encoding="utf-8").splitlines()
    return [parse_slot(line.strip()) for line in lines if line.strip()]


def _load(folder: Path) -> tuple[list[str], list[RGBImage], Assignment]:
    """Decodable photos in file-name order, with the assignment by shooting order."""
    names: list[str] = []
    images: list[RGBImage] = []
    times = []
    for path in sorted(p for p in folder.iterdir() if p.is_file() and p.name != "order.txt"):
        try:
            photo = decode(path.read_bytes())
        except UnsupportedFormatError:
            continue
        names.append(path.name)
        images.append(resize_long_edge(photo.rgb, WORKING_LONG_EDGE)[0])
        times.append(capture_time(photo, path)[0])
    return names, images, assign_by_order(times, _order(folder))


@pytest.mark.parametrize("folder", _sets(), ids=lambda p: p.name)
def test_composite_set_metrics(folder: Path) -> None:
    names, images, assignment = _load(folder)
    plan = plan_composite(images, assignment, flips=DEFAULT_FLIPS)
    rows = [
        {
            "slot": slot,
            "photo": names[cell.photo],
            "level_deg": round(cell.geometry.level.angle_deg, 2),
            "confident": cell.geometry.level.confident,
        }
        for slot, cell in plan.cells.items()
    ]
    REPORTS.mkdir(parents=True, exist_ok=True)
    report = REPORTS / f"composite_{folder.name}.csv"
    with report.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["slot", "photo", "level_deg", "confident"])
        writer.writeheader()
        writer.writerows(rows)
    assert rows, f"no photos placed in {folder}"


def test_classifier_leave_one_set_out() -> None:
    model_path = cached_model(DEFAULT_MODEL, paths.model_cache_dir())
    if model_path is None:
        pytest.skip("the embedding model is not in the model cache")
    folders = _sets()
    if len(folders) < MIN_SETS:
        pytest.skip(f"leave-one-set-out needs at least {MIN_SETS} sets")

    embedder = Embedder(model_path, DEFAULT_MODEL.sha256)
    pairs: list[EmbeddingPair] = []
    truths: list[list[Slot]] = []
    for folder in folders:
        _, images, assignment = _load(folder)
        placed = [i for i, slot in enumerate(assignment.slots) if slot is not None]
        pairs.append(embedder.embed([images[i] for i in placed]))
        truths.append([slot for slot in assignment.slots if slot is not None])

    rows = []
    for regularization in REGULARIZATIONS:
        correct = total = 0
        for held_out, truth in enumerate(truths):
            train = [i for i in range(len(folders)) if i != held_out]
            classifier = train_classifier(
                _concat([pairs[i] for i in train]),
                [slot for i in train for slot in truths[i]],
                DEFAULT_MODEL.sha256,
                regularization=regularization,
            )
            if classifier is None:  # a slot has too few samples in the training sets
                continue
            predicted = assign_by_classifier(classifier.slot_probabilities(pairs[held_out]))
            correct += sum(p == t for p, t in zip(predicted.slots, truth, strict=True))
            total += len(truth)
        rows.append(
            {
                "C": regularization,
                "accuracy": round(correct / total, 4) if total else float("nan"),
                "photos": total,
            }
        )
    REPORTS.mkdir(parents=True, exist_ok=True)
    with (REPORTS / "composite_classifier.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["C", "accuracy", "photos"])
        writer.writeheader()
        writer.writerows(rows)
    assert any(row["photos"] for row in rows), "no regularization could be trained"


def _concat(pairs: Sequence[EmbeddingPair]) -> EmbeddingPair:
    return EmbeddingPair(
        original=_stack([p.original for p in pairs]), mirrored=_stack([p.mirrored for p in pairs])
    )


def _stack(blocks: Sequence[npt.NDArray[np.float32]]) -> npt.NDArray[np.float32]:
    return np.concatenate(blocks)
