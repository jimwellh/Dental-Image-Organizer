"""Store confirmed samples and retrain the slot classifier (spec §4.6)."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from dental_image_organizer.core.slots import Slot
from dental_image_organizer.ml.classifier import train_classifier
from dental_image_organizer.ml.embedder import EmbeddingPair
from dental_image_organizer.ml.samples import SampleSource, SampleStore

CLASSIFIER_FILE = "model.json"


def learn(
    directory: Path,
    pairs: EmbeddingPair,
    slots: Sequence[Slot],
    *,
    photo_ids: Sequence[str],
    case_id: str,
    source: SampleSource,
    model_hash: str,
    timestamp: float,
) -> bool:
    """Store the placed photos as confirmed samples and retrain; True once the classifier is.

    Callers pass a random `case_id` and content-hash `photo_ids`, which keep patient names out
    of the data folder. Callers that share the folder must serialize calls.
    """
    SampleStore(directory).append(
        pairs,
        slots,
        photo_ids=photo_ids,
        case_id=case_id,
        source=source,
        model_hash=model_hash,
        timestamp=timestamp,
    )
    return retrain(directory, model_hash)


def retrain(directory: Path, model_hash: str) -> bool:
    """Train from the stored samples of `model_hash`; True once the classifier is trained."""
    current = SampleStore(directory).load().for_model(model_hash)
    classifier = train_classifier(current.pairs, current.slots, model_hash)
    if classifier is None:
        # A file left from before (damaged, another model's, or trained on labels since
        # replaced) no longer matches the samples; the next run must agree with "not trained".
        (directory / CLASSIFIER_FILE).unlink(missing_ok=True)
        return False
    classifier.save(directory / CLASSIFIER_FILE)
    return True
