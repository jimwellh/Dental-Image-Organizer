"""Teaching sessions: past composite sets become classifier samples and flips (spec §3.5).

A session lives in a temporary folder of the cache and in memory; nothing reaches the library, and
no patient name is asked for. The folder is deleted on confirm, on cancel, and at startup.
"""

from __future__ import annotations

import shutil
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import numpy as np
import structlog
from structlog.contextvars import bound_contextvars

from dental_image_organizer.core.imageio import decode, encode_jpeg, resize_long_edge
from dental_image_organizer.core.slots import FLIPS, SLOTS, Flip, Slot
from dental_image_organizer.errors import (
    InvalidRequestError,
    ModelUnavailableError,
    NotReadyError,
    PhotoNotFoundError,
    TeachSessionNotFoundError,
)
from dental_image_organizer.ml.embedder import EmbeddingPair
from dental_image_organizer.services.ai import ClassifierStatus
from dental_image_organizer.services.batch_io import failure_message, header_capture_time
from dental_image_organizer.services.composite_case import composite_flips
from dental_image_organizer.services.intake import (
    Skipped,
    Staged,
    Upload,
    stage_upload,
)
from dental_image_organizer.services.jobs import JobProgress
from dental_image_organizer.services.renders import (
    PREVIEW_EDGE,
    PREVIEW_QUALITY,
    THUMB_EDGE,
)
from dental_image_organizer.services.shooting_order import OrderedPhoto, shooting_slots
from dental_image_organizer.storage.library import local_now
from dental_image_organizer.storage.schema import PhotoState, new_id

if TYPE_CHECKING:
    from dental_image_organizer.services.context import AppContext

log = structlog.get_logger(__name__)

SessionState = Literal["processing", "ready", "failed"]
TeachSize = Literal["thumb", "preview"]

NEEDS_MODEL = "Download the AI model first; teaching needs it."
REPEAT_MESSAGE = "This photo is already in the set."


@dataclass(frozen=True)
class TeachPhoto:
    id: str
    name: str
    captured_at: datetime | None
    state: PhotoState
    error: str | None


@dataclass(frozen=True)
class TeachSession:
    id: str
    state: SessionState
    photos: tuple[TeachPhoto, ...]
    proposal: Mapping[Slot, str]
    job_id: str


@dataclass
class _Entry:
    id: str
    folder: Path
    staged: list[Staged]
    photos: list[TeachPhoto]
    state: SessionState = "processing"
    proposal: dict[Slot, str] = field(default_factory=dict)
    job_id: str = ""
    # Embeddings of the ready photos, by photo id; they never leave memory.
    rows: dict[str, int] = field(default_factory=dict)
    pairs: EmbeddingPair | None = None
    confirming: bool = False
    # Serializes the job's file writes with the folder's deletion, so a cancel cannot be
    # followed by a write that recreates the folder or a delete that races a write.
    files: threading.Lock = field(default_factory=threading.Lock)
    cancelled: bool = False

    def snapshot(self) -> TeachSession:
        return TeachSession(
            self.id, self.state, tuple(self.photos), dict(self.proposal), self.job_id
        )


class _CancelledError(Exception):
    """The session was cancelled while its job ran; the job stops quietly."""


class TeachSessions:
    def __init__(self, ctx: AppContext) -> None:
        self._ctx = ctx
        # Guards the session table and each entry's mutable fields. Never held while learning,
        # embedding, or doing file I/O beyond a lookup.
        self._lock = threading.Lock()
        self._sessions: dict[str, _Entry] = {}

    # -- start ---------------------------------------------------------------------------

    def start(self, uploads: Sequence[Upload]) -> tuple[TeachSession | None, list[Skipped]]:
        ctx = self._ctx
        if ctx.ai.embedder() is None:
            raise ModelUnavailableError(NEEDS_MODEL)
        session_id = new_id()
        folder = ctx.dirs.teach / session_id
        staged: list[Staged] = []
        skipped: list[Skipped] = []
        try:
            folder.mkdir(parents=True)
            seen: set[str] = set()
            for item in uploads:
                outcome = stage_upload(item, folder)
                if isinstance(outcome, Skipped):
                    skipped.append(outcome)
                elif outcome.sha256 in seen:
                    skipped.append(Skipped(outcome.name, "duplicate", REPEAT_MESSAGE))
                else:
                    seen.add(outcome.sha256)
                    staged.append(outcome)
        except BaseException:
            _remove(folder)
            raise
        if not staged:
            _remove(folder)
            return None, skipped
        photos = [TeachPhoto(new_id(), s.name, None, "pending", None) for s in staged]
        entry = _Entry(session_id, folder, staged, photos)
        with self._lock:
            self._sessions[session_id] = entry

        def run(progress: JobProgress) -> None:
            self._process(entry, progress)

        # The job may finish before `job_id` is set; it never reads that field.
        job = ctx.jobs.submit("teach", run, key=f"teach:{session_id}", total=len(staged))
        with self._lock:
            entry.job_id = job.id
            snapshot = entry.snapshot()
        log.info("teach_started", session_id=session_id, job_id=job.id, photos=len(staged))
        return snapshot, skipped

    def _process(self, entry: _Entry, progress: JobProgress) -> None:
        with bound_contextvars(session_id=entry.id):
            try:
                self._run(entry, progress)
            except _CancelledError:
                log.info("teach_cancelled_while_processing")
            except Exception:
                log.exception("teach_failed")
                with self._lock:
                    if not entry.cancelled:
                        entry.state = "failed"
                raise

    def _run(self, entry: _Entry, progress: JobProgress) -> None:
        ctx = self._ctx
        embedder = ctx.ai.embedder()
        if embedder is None:
            raise ModelUnavailableError(NEEDS_MODEL)
        total = len(entry.staged)
        working: list[tuple[str, np.ndarray]] = []
        for index, staged in enumerate(entry.staged):
            photo = entry.photos[index]
            self._check(entry)
            path = entry.folder / staged.file_name
            try:
                decoded = decode(path.read_bytes())
                taken = decoded.metadata.captured_at or staged.last_modified or local_now()
                thumb, _ = resize_long_edge(decoded.rgb, THUMB_EDGE)
                preview, _ = resize_long_edge(decoded.rgb, PREVIEW_EDGE)
                thumb_jpeg = encode_jpeg(thumb, None, quality=PREVIEW_QUALITY)
                preview_jpeg = encode_jpeg(preview, None, quality=PREVIEW_QUALITY)
            except Exception as exc:
                # One bad photo never aborts a batch (spec §4.13); its rank stays as a hole.
                log.error("photo_unreadable", photo_id=photo.id, error=failure_message(exc))
                self._update(
                    entry, index, replace(photo, state="failed", error=failure_message(exc))
                )
            else:
                self._write(entry, photo.id, thumb_jpeg, preview_jpeg)
                self._update(entry, index, replace(photo, state="ready", captured_at=taken))
                working.append((photo.id, preview))
            progress(index + 1, total)
        self._check(entry)
        pairs = embedder.embed([image for _, image in working]) if working else None
        with self._lock:
            if entry.cancelled:
                raise _CancelledError
            entry.pairs = pairs
            entry.rows = {photo_id: row for row, (photo_id, _) in enumerate(working)}
            entry.proposal = self._propose(entry)
            entry.state = "ready"
        log.info("teach_ready", ready=len(working), placed=len(entry.proposal))

    def _propose(self, entry: _Entry) -> dict[Slot, str]:
        """Slots in shooting order; a photo that failed to decode holds its rank as a hole."""
        order = self._ctx.settings.get().shooting_order
        ordered: list[OrderedPhoto] = []
        for key, (photo, staged) in enumerate(zip(entry.photos, entry.staged, strict=True)):
            if photo.state == "ready":
                ordered.append(OrderedPhoto(key, photo.name, photo.captured_at))
            else:
                taken = header_capture_time(entry.folder / staged.file_name)
                ordered.append(OrderedPhoto(key, photo.name, taken, usable=False))
        return {slot: entry.photos[key].id for key, slot in shooting_slots(ordered, order).items()}

    def _check(self, entry: _Entry) -> None:
        with self._lock:
            if entry.cancelled:
                raise _CancelledError

    def _update(self, entry: _Entry, index: int, photo: TeachPhoto) -> None:
        with self._lock:
            entry.photos[index] = photo

    def _write(self, entry: _Entry, photo_id: str, thumb: bytes, preview: bytes) -> None:
        with entry.files:
            if entry.cancelled:
                raise _CancelledError
            (entry.folder / f"{photo_id}.thumb.jpg").write_bytes(thumb)
            (entry.folder / f"{photo_id}.preview.jpg").write_bytes(preview)

    # -- queries -------------------------------------------------------------------------

    def get(self, session_id: str) -> TeachSession:
        with self._lock:
            return self._entry(session_id).snapshot()

    def image(self, session_id: str, photo_id: str, size: TeachSize) -> bytes:
        with self._lock:
            entry = self._entry(session_id)
            photo = next((p for p in entry.photos if p.id == photo_id), None)
            if photo is None:
                raise PhotoNotFoundError("This photo is not in the teaching session.")
            if photo.state != "ready":
                raise NotReadyError("This photo has no image yet.")
        path = entry.folder / f"{photo_id}.{size}.jpg"
        with entry.files:
            if entry.cancelled:
                raise TeachSessionNotFoundError("This teaching session no longer exists.")
            return path.read_bytes()

    def _entry(self, session_id: str) -> _Entry:
        entry = self._sessions.get(session_id)
        if entry is None:
            raise TeachSessionNotFoundError("This teaching session no longer exists.")
        return entry

    # -- confirm and cancel --------------------------------------------------------------

    def confirm(
        self, session_id: str, slots: Mapping[Slot, str], flips: Mapping[Slot, Flip]
    ) -> ClassifierStatus:
        ctx = self._ctx
        with bound_contextvars(session_id=session_id):
            # Read before the claim: it takes the AI lock, which the sessions lock must not
            # be held around.
            defaults = composite_flips(ctx)
            with self._lock:
                entry = self._entry(session_id)
                if entry.state != "ready" or entry.confirming or entry.pairs is None:
                    raise NotReadyError("The teaching session is not ready to confirm.")
                placed = self._validate(slots, flips)
                ready = {p.id for p in entry.photos if p.state == "ready"}
                if not set(slots.values()) <= ready:
                    raise InvalidRequestError("Every slot needs a photo of this session.")
                hashes = {p.id: s.sha256 for p, s in zip(entry.photos, entry.staged, strict=True)}
                rows = [entry.rows[slots[slot]] for slot in placed]
                photo_ids = [hashes[slots[slot]] for slot in placed]
                pairs = entry.pairs.take(rows)
                entry.confirming = True
            confirmed = {slot: flips.get(slot, defaults[slot]) for slot in placed}
            case_id = f"teach:{session_id}"
            # Outside the sessions lock: learning retrains under the AI lock.
            try:
                ctx.ai.learn(pairs, placed, photo_ids=photo_ids, case_id=case_id, source="teach")
                ctx.ai.record_flips(case_id, confirmed, pins=ctx.settings.get().flip_pins)
            except BaseException:
                with self._lock:
                    entry.confirming = False
                raise
            self._discard(session_id)
            log.info("teach_confirmed", samples=len(placed))
            return ctx.ai.status()

    @staticmethod
    def _validate(slots: Mapping[Slot, str], flips: Mapping[Slot, Flip]) -> list[Slot]:
        """The placed slots in board order, after checking the request's shape."""
        if not slots:
            raise InvalidRequestError("Place at least one photo before confirming.")
        if not set(slots) <= set(SLOTS):
            raise InvalidRequestError("Unknown slot.")
        if len(set(slots.values())) != len(slots):
            raise InvalidRequestError("A photo can fill only one slot.")
        if not set(flips) <= set(slots):
            raise InvalidRequestError("A flip needs a placed slot.")
        if not set(flips.values()) <= set(FLIPS):
            raise InvalidRequestError("Unknown flip.")
        return [slot for slot in SLOTS if slot in slots]

    def cancel(self, session_id: str) -> None:
        with bound_contextvars(session_id=session_id):
            self._entry_exists(session_id)
            self._discard(session_id)
            log.info("teach_cancelled")

    def _entry_exists(self, session_id: str) -> None:
        with self._lock:
            self._entry(session_id)

    def _discard(self, session_id: str) -> None:
        with self._lock:
            entry = self._sessions.pop(session_id, None)
            if entry is not None:
                entry.cancelled = True
        if entry is not None:
            with entry.files:
                _remove(entry.folder)


def _remove(folder: Path) -> None:
    try:
        shutil.rmtree(folder)
    except FileNotFoundError:
        pass
    except OSError as exc:
        # Startup empties the folder anyway; a leftover only costs disk space until then.
        log.warning("teach_folder_remove_failed", error=exc.strerror or type(exc).__name__)
