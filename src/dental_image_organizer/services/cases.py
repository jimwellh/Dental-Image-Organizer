"""Case and patient changes that span both case types (spec §3.2-§3.4, §4.11, §4.12).

Renames and deletions are refused while a job of the case, or the patient's export, runs: an
export names its files by the patient and title it read when it started, and a job whose case
went to the trash could only fail.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import structlog
from structlog.contextvars import bound_contextvars

from dental_image_organizer.core.slots import SLOTS, Flip, Slot
from dental_image_organizer.errors import InvalidRequestError, JobRunningError
from dental_image_organizer.services import composite_case, procedure_case
from dental_image_organizer.services.export import patient_export_key
from dental_image_organizer.services.intake import Upload, UploadOutcome
from dental_image_organizer.storage.schema import (
    CaseRecord,
    CaseType,
    CompositeRecord,
    PatientRecord,
    PhotoRecord,
    SequenceRecord,
)

if TYPE_CHECKING:
    from dental_image_organizer.services.context import AppContext

log = structlog.get_logger(__name__)

MAX_THUMBNAILS = 4

_ONE_PATIENT_MESSAGE = "Choose an existing patient or enter a new patient's name, not both."
_NOTE_MESSAGE = "A note belongs to a new patient; change an existing patient's note instead."
_REFERENCE_MESSAGE = "Replace the reference instead of deleting it."
_RENAME_BUSY_MESSAGE = "Wait for the running jobs of this case to finish before renaming it."
_DELETE_BUSY_MESSAGE = "Wait for the running jobs of this case to finish before deleting it."
_PATIENT_BUSY_MESSAGE = "Wait for the running jobs of this patient to finish before renaming."


@dataclass(frozen=True)
class CaseDetails:
    case: CaseRecord
    patient: PatientRecord
    folder: str  # relative to the library root, for Reveal in Finder
    suggested_output_scale: float | None
    flip_defaults: dict[Slot, Flip] | None


def create_case(
    ctx: AppContext,
    kind: CaseType,
    title: str,
    *,
    patient_id: str | None = None,
    patient_name: str | None = None,
    patient_note: str | None = None,
) -> CaseRecord:
    """A new case for an existing patient, or for a new patient created with it."""
    if patient_name is None:
        if patient_id is None:
            raise InvalidRequestError(_ONE_PATIENT_MESSAGE)
        if patient_note is not None:
            raise InvalidRequestError(_NOTE_MESSAGE)
        return _add_case(ctx, patient_id, kind, title)
    if patient_id is not None:
        raise InvalidRequestError(_ONE_PATIENT_MESSAGE)
    library = ctx.library()
    created = library.create_patient(patient_name, patient_note).id
    try:
        return _add_case(ctx, created, kind, title)
    except BaseException:
        # A title that cannot name a folder must not leave a patient without the case behind.
        library.remove_empty_patient(created)
        raise


def _add_case(ctx: AppContext, patient_id: str, kind: CaseType, title: str) -> CaseRecord:
    block = composite_case.composite_block(ctx.settings.get()) if kind == "composite" else None
    case = ctx.library().create_case(patient_id, kind, title, composite=block)
    log.info("case_created", case_id=case.id, case_type=kind)
    return case


def case_details(ctx: AppContext, case_id: str) -> CaseDetails:
    return details_of(ctx, ctx.library().read_case(case_id))


def details_of(ctx: AppContext, case: CaseRecord) -> CaseDetails:
    """What the case screen shows besides the record, for a case as read or as just changed."""
    library = ctx.library()
    patient = library.patient_record(case.patient_id)
    folder = library.relative(library.case_dir(case.id))
    if case.type == "composite":
        return CaseDetails(case, patient, folder, None, composite_case.composite_flips(ctx))
    return CaseDetails(case, patient, folder, procedure_case.suggested_output_scale(case), None)


def change_case(
    ctx: AppContext,
    case_id: str,
    *,
    title: str | None = None,
    output_scale: float | None = None,
    sequence: SequenceRecord | None = None,
    layout: CompositeRecord | None = None,
) -> CaseRecord:
    """Apply each given change in turn; one that is refused leaves the earlier ones applied."""
    with bound_contextvars(case_id=case_id):
        library = ctx.library()
        case = library.read_case(case_id)
        # A form sent whole repeats the title; only a real rename waits for the jobs.
        if title is not None and _display(title) != case.title:
            _refuse_while_busy(ctx, case, _RENAME_BUSY_MESSAGE)
            case = library.rename_case(case_id, title)
            log.info("case_renamed")
        if output_scale is not None:
            case = procedure_case.set_output_scale(ctx, case_id, output_scale)
        if sequence is not None:
            case = procedure_case.set_sequence(ctx, case_id, sequence.photo_ids, sequence.gap_px)
        if layout is not None:
            case = composite_case.set_layout(ctx, case_id, layout)
        return case


def delete_case(ctx: AppContext, case_id: str) -> None:
    """Move the case folder to `.trash`, where Finder can restore it (spec §4.11)."""
    with bound_contextvars(case_id=case_id):
        library = ctx.library()
        _refuse_while_busy(ctx, library.read_case(case_id), _DELETE_BUSY_MESSAGE)
        library.trash_case(case_id)
        log.info("case_trashed")


def update_patient(
    ctx: AppContext, patient_id: str, *, name: str, note: str | None
) -> PatientRecord:
    with bound_contextvars(patient_id=patient_id):
        library = ctx.library()
        entry = library.patient(patient_id)
        if _display(name) != entry.record.name:
            jobs = [*(c.id for c in entry.cases), patient_export_key(patient_id)]
            if ctx.jobs.active_any(jobs):
                raise JobRunningError(_PATIENT_BUSY_MESSAGE)
        record = library.update_patient(patient_id, name=name, note=note)
        log.info("patient_updated")
        return record


def upload_photos(ctx: AppContext, case_id: str, uploads: Sequence[Upload]) -> UploadOutcome:
    if ctx.library().read_case(case_id).type == "composite":
        return composite_case.upload(ctx, case_id, uploads)
    return procedure_case.upload(ctx, case_id, uploads)


def delete_photo(ctx: AppContext, case_id: str, photo_id: str) -> CaseRecord:
    """Remove a photo, and its original once no record uses the file."""
    removed: list[PhotoRecord] = []
    # Read before the case lock, as composite_case does: it waits while training runs.
    composite = ctx.library().read_case(case_id).type == "composite"
    defaults = composite_case.composite_flips(ctx) if composite else {}

    def change(case: CaseRecord) -> CaseRecord:
        removed.clear()
        photo = case.photo(photo_id)
        if photo.role == "reference":
            raise InvalidRequestError(_REFERENCE_MESSAGE)
        removed.append(photo)
        update: dict[str, Any] = {"photos": [p for p in case.photos if p.id != photo_id]}
        if case.sequence is not None:
            kept = [i for i in case.sequence.photo_ids if i != photo_id]
            # A selection set_sequence would refuse is never stored; the default capture
            # order takes its place.
            if len(kept) < procedure_case.SEQUENCE_SIZE[0]:
                kept = []
            update["sequence"] = case.sequence.model_copy(update={"photo_ids": kept})
        block = case.composite
        if block is not None and block.baseline_photo_id == photo_id:
            update["composite"] = block.model_copy(update={"baseline_photo_id": None})
        trimmed = case.model_copy(update=update)
        if case.type == "composite":
            # Colors are fitted across the cells, so the others change with the one removed.
            return composite_case.replan(ctx, trimmed, defaults)
        # The earliest photo may be gone, which moves the day the stage rule counts from.
        return procedure_case.restaged(trimmed)

    with bound_contextvars(case_id=case_id, photo_id=photo_id):
        updated = ctx.library().update_case(case_id, change)
        procedure_case.remove_unused_original(ctx, case_id, removed[0])
        log.info("photo_deleted")
        return updated


def resume_pending(ctx: AppContext) -> int:
    """Queue processing for each case whose photos a closed or crashed run left pending.

    Quitting fails the queued jobs and a crash loses them, while their photos stay pending in
    case.json. The server calls this once at startup; the number of cases queued.
    """
    resumed = 0
    for entry in ctx.library().listing().patients:
        for case in entry.cases:
            pending = [p.id for p in case.photos if p.state == "pending"]
            if not pending:
                continue
            if case.type == "composite":
                composite_case.submit_processing(ctx, case.id, pending)
            else:
                procedure_case.resume_processing(ctx, case)
            resumed += 1
    log.info("pending_photos_resumed", cases=resumed)
    return resumed


def thumbnails(case: CaseRecord) -> list[str]:
    """Up to four ready photos for the case's card: the reference first and then capture order
    for a procedure case, slot order for a composite case."""
    ready = [p for p in case.photos if p.state == "ready"]
    if case.type == "composite":
        cells = sorted((SLOTS.index(p.slot), p.id) for p in ready if p.slot is not None)
        ids = [photo_id for _, photo_id in cells]
    else:
        ordered = sorted(
            ready, key=lambda p: (p.role != "reference", procedure_case.capture_order(p))
        )
        ids = [p.id for p in ordered]
    return ids[:MAX_THUMBNAILS]


def _refuse_while_busy(ctx: AppContext, case: CaseRecord, message: str) -> None:
    if ctx.jobs.active_any([case.id, patient_export_key(case.patient_id)]):
        raise JobRunningError(message)


def _display(text: str) -> str:
    # The form Library stores names and titles in, so an unchanged one never counts as a rename.
    return unicodedata.normalize("NFC", text).strip()
