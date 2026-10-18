"""Two-head slot classifier: a view-group head and a mirror-antisymmetric side head (spec §4.6)."""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import numpy as np
import numpy.typing as npt
import structlog
from sklearn.linear_model import LogisticRegression

from dental_image_organizer.core.slots import (
    GROUPS,
    SLOTS,
    Slot,
    SlotGroup,
    slot_group,
    slot_side,
)
from dental_image_organizer.errors import StorageError
from dental_image_organizer.ml.embedder import EMBEDDING_DIM, EmbeddingPair

MIN_SAMPLES_PER_SLOT = 3
CLASSIFIER_C = 10.0  # spec amendment 1
MAX_ITERATIONS = 2000
LATERAL_GROUPS = ("upper_buccal", "occlusion_side", "lower_buccal")

_log = structlog.get_logger(__name__)
Floats = npt.NDArray[np.float64]


def _features(pair: EmbeddingPair) -> tuple[Floats, Floats]:
    g = pair.original.astype(np.float64)
    m = pair.mirrored.astype(np.float64)
    # The symmetric part identifies the view; the antisymmetric part carries the side.
    return (g + m) / 2, g - m


@dataclass(frozen=True)
class SlotClassifier:
    model_hash: str
    group_classes: tuple[SlotGroup, ...]
    group_coef: Floats
    group_intercept: Floats
    side_coef: Floats

    def side_scores(self, pair: EmbeddingPair) -> Floats:
        """Positive means right. Swapping original and mirrored negates it exactly."""
        _, a = _features(pair)
        scores: Floats = a @ self.side_coef
        return scores

    def slot_probabilities(self, pair: EmbeddingPair) -> Floats:
        s, _ = _features(pair)
        logits = s @ self.group_coef.T + self.group_intercept
        logits -= logits.max(axis=1, keepdims=True)
        exp = np.exp(logits)
        p_group = exp / exp.sum(axis=1, keepdims=True)
        p_right = 1.0 / (1.0 + np.exp(-self.side_scores(pair)))
        out = np.empty((len(p_group), len(SLOTS)), np.float64)
        for column, slot in enumerate(SLOTS):
            p = p_group[:, self.group_classes.index(slot_group(slot))]
            side = slot_side(slot)
            out[:, column] = (
                p if side is None else p * (p_right if side == "right" else 1 - p_right)
            )
        return out

    def save(self, path: Path) -> None:
        payload = {
            "model_hash": self.model_hash,
            "group_classes": list(self.group_classes),
            "group_coef": self.group_coef.tolist(),
            "group_intercept": self.group_intercept.tolist(),
            "side_coef": self.side_coef.tolist(),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload))
            # Without the fsync, a power loss soon after the rename can leave an empty file.
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)


def load_classifier(path: Path, model_hash: str) -> SlotClassifier | None:
    """The saved classifier, or None if there is none or it belongs to another model.

    Raises StorageError when the file cannot be read back.
    """
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise TypeError(f"expected a JSON object, got {type(data).__name__}")
        # A file without a hash cannot be vouched for, so it counts as another model's.
        if data.get("model_hash") != model_hash:
            return None
        classifier = SlotClassifier(
            model_hash,
            cast("tuple[SlotGroup, ...]", tuple(data["group_classes"])),
            np.asarray(data["group_coef"], np.float64),
            np.asarray(data["group_intercept"], np.float64),
            np.asarray(data["side_coef"], np.float64),
        )
        _check_shapes(classifier)
    except (OSError, KeyError, TypeError, ValueError) as exc:  # JSONDecodeError is a ValueError
        _log.warning("classifier_damaged", error=type(exc).__name__)
        raise StorageError("The AI classifier file is damaged") from exc
    return classifier


def _check_shapes(classifier: SlotClassifier) -> None:
    # A file that parses but does not fit would otherwise fail later, while scoring photos.
    groups = len(GROUPS)
    if (
        sorted(classifier.group_classes) != sorted(GROUPS)
        or classifier.group_coef.shape != (groups, EMBEDDING_DIM)
        or classifier.group_intercept.shape != (groups,)
        or classifier.side_coef.shape != (EMBEDDING_DIM,)
    ):
        raise ValueError("the classifier arrays do not fit the embedding model")


def train_classifier(
    pair: EmbeddingPair,
    slots: Sequence[Slot],
    model_hash: str,
    *,
    regularization: float = CLASSIFIER_C,
) -> SlotClassifier | None:
    counts = dict.fromkeys(SLOTS, 0)
    for slot in slots:
        counts[slot] += 1
    if min(counts.values()) < MIN_SAMPLES_PER_SLOT:
        return None

    s, a = _features(pair)
    groups = np.array([slot_group(slot) for slot in slots])
    group_head = LogisticRegression(
        C=regularization, class_weight="balanced", max_iter=MAX_ITERATIONS
    ).fit(s, groups)

    sides = np.array([slot_side(slot) or "" for slot in slots])
    lateral = sides != ""
    # Adding each mirror with the opposite label makes the learned axis antisymmetric by design.
    features = np.concatenate([a[lateral], -a[lateral]])
    labels = np.concatenate([sides[lateral], np.where(sides[lateral] == "right", "left", "right")])
    side_head = LogisticRegression(
        C=regularization, fit_intercept=False, max_iter=MAX_ITERATIONS
    ).fit(features, labels)
    coef = side_head.coef_[0]
    side_coef = coef if side_head.classes_[1] == "right" else -coef

    _log.debug("classifier_trained", samples=len(slots))
    return SlotClassifier(
        model_hash,
        cast("tuple[SlotGroup, ...]", tuple(str(c) for c in group_head.classes_)),
        group_head.coef_.astype(np.float64),
        group_head.intercept_.astype(np.float64),
        side_coef.astype(np.float64),
    )
