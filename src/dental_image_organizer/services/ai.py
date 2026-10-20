"""One verified model, embedder, and classifier shared by the whole server (spec §4.6)."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import httpx
import structlog

from dental_image_organizer.core.slots import SLOTS, Flip, Slot
from dental_image_organizer.errors import ModelUnavailableError, StorageError
from dental_image_organizer.ml import training
from dental_image_organizer.ml.classifier import SlotClassifier, load_classifier
from dental_image_organizer.ml.embedder import Embedder, EmbeddingPair
from dental_image_organizer.ml.model_store import (
    DEFAULT_MODEL,
    ModelSpec,
    cached_model,
    download_model,
)
from dental_image_organizer.ml.samples import SampleSource, SampleStore
from dental_image_organizer.paths import AppDirs
from dental_image_organizer.services.flips import FLIPS_FILE, FlipConfirmation, FlipStore

log = structlog.get_logger(__name__)

AssignReason = Literal[
    "untrained", "no_model", "stale_model", "damaged_classifier", "embedding_failed"
]

_Signature = tuple[int, int]


@dataclass(frozen=True)
class ClassifierStatus:
    trained: bool
    counts: dict[Slot, int]
    stale_samples: int
    damaged: str | None
    model_available: bool


def _signature(path: Path) -> _Signature | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_size, stat.st_mtime_ns


class AiRuntime:
    def __init__(
        self,
        dirs: AppDirs,
        spec: ModelSpec = DEFAULT_MODEL,
        *,
        http_client: Callable[[], httpx.Client] | None = None,
    ) -> None:
        self.dirs = dirs
        self.spec = spec
        self.flips = FlipStore(dirs.classifier / FLIPS_FILE)
        # Serializes every write under dirs.classifier (samples, model.json, flips.json). Never
        # held around an embedding call or a download.
        self.lock = threading.RLock()
        self._http_client = http_client
        # Guards the caches below; separate from `lock` so hashing the model never blocks learning.
        self._state = threading.Lock()
        self._verified: _Signature | None = None
        self._embedder: Embedder | None = None
        self._classifier: tuple[_Signature, SlotClassifier] | None = None

    def model_path(self) -> Path | None:
        """The verified model file, or None; re-hashed only when the file changes."""
        path = self.dirs.models / self.spec.filename
        with self._state:
            signature = _signature(path)
            if signature is not None and signature == self._verified:
                return path
            self._verified = None
            self._embedder = None
            if signature is None:
                return None
            try:
                found = cached_model(self.spec, self.dirs.models)
            except ModelUnavailableError as exc:
                # The message may carry the cache path; the code is enough to diagnose.
                log.warning("model_unreadable", code=exc.code)
                return None
            if found is None:
                return None
            # The signature from before the hash: a file replaced meanwhile is verified again.
            self._verified = signature
            return found

    def embedder(self) -> Embedder | None:
        path = self.model_path()
        if path is None:
            return None
        with self._state:
            if self._embedder is None:
                try:
                    self._embedder = Embedder(path, self.spec.sha256)
                except ModelUnavailableError as exc:
                    log.warning("model_unloadable", code=exc.code)
                    return None
            return self._embedder

    def download(self, progress: Callable[[int, int], None] | None = None) -> None:
        # No lock: the progress callback runs inside the download.
        if self._http_client is None:
            download_model(self.spec, self.dirs.models, progress=progress)
        else:
            with self._http_client() as client:
                download_model(self.spec, self.dirs.models, client=client, progress=progress)
        if self.model_path() is None:
            raise ModelUnavailableError("The downloaded AI model could not be verified.")

    def classifier(self) -> tuple[SlotClassifier | None, AssignReason | None]:
        file = self.dirs.classifier / training.CLASSIFIER_FILE
        if not file.exists():
            return None, "untrained"
        if self.model_path() is None:
            return None, "no_model"
        with self.lock:
            signature = _signature(file)
            with self._state:
                cached = self._classifier
            if cached is not None and signature == cached[0]:
                return cached[1], None
            try:
                loaded = load_classifier(file, self.spec.sha256)
            except StorageError:
                return None, "damaged_classifier"
            if loaded is None or signature is None:
                return None, "stale_model"
            with self._state:
                self._classifier = (signature, loaded)
            return loaded, None

    def status(self) -> ClassifierStatus:
        counts: dict[Slot, int] = dict.fromkeys(SLOTS, 0)
        stale = 0
        damaged: str | None = None
        try:
            with self.lock:
                samples = SampleStore(self.dirs.classifier).load()
            current = samples.for_model(self.spec.sha256)
            counts = current.counts()
            stale = len(samples.slots) - len(current.slots)
        except StorageError as exc:
            damaged = str(exc)
        trained = damaged is None and self.classifier()[0] is not None
        return ClassifierStatus(trained, counts, stale, damaged, self.model_path() is not None)

    def learn(
        self,
        pairs: EmbeddingPair,
        slots: Sequence[Slot],
        *,
        photo_ids: Sequence[str],
        case_id: str,
        source: SampleSource,
    ) -> bool:
        with self.lock:
            trained = training.learn(
                self.dirs.classifier,
                pairs,
                slots,
                photo_ids=photo_ids,
                case_id=case_id,
                source=source,
                model_hash=self.spec.sha256,
                timestamp=time.time(),
            )
            self._drop_classifier()
            return trained

    def retrain(self) -> bool:
        with self.lock:
            trained = training.retrain(self.dirs.classifier, self.spec.sha256)
            self._drop_classifier()
            return trained

    def _drop_classifier(self) -> None:
        # A same-size rewrite within one mtime granule leaves the file signature unchanged, so
        # the signature alone cannot tell that a retrain replaced the classifier.
        with self._state:
            self._classifier = None

    def flip_history(self) -> tuple[FlipConfirmation, ...]:
        # Reads hold the lock too: on Windows a read racing os.replace fails with PermissionError.
        with self.lock:
            return self.flips.load()

    def record_flips(
        self, case_id: str, flips: Mapping[Slot, Flip], *, pins: Mapping[Slot, Flip]
    ) -> None:
        with self.lock:
            self.flips.record(case_id, flips, pins=pins, timestamp=time.time())
