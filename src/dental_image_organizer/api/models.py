"""Request and response bodies of the HTTP API (spec §4.12, §4.13).

The frontend's TypeScript types are generated from the OpenAPI schema of these models, so every
body is a model, and responses reuse the `storage.schema` records rather than copies of them.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dental_image_organizer.core.align import AlignModel
from dental_image_organizer.core.slots import Flip, Slot
from dental_image_organizer.services import cases
from dental_image_organizer.services.ai import ClassifierStatus
from dental_image_organizer.services.cases import CaseDetails
from dental_image_organizer.services.export import ConflictMode, ExportInclude
from dental_image_organizer.services.flips import FlipStatus
from dental_image_organizer.services.intake import UploadOutcome, UploadSkipReason
from dental_image_organizer.services.jobs import JobSnapshot, JobState
from dental_image_organizer.services.teach import SessionState, TeachSession
from dental_image_organizer.services.updates import UpdateInfo
from dental_image_organizer.storage.archive import ImportOutcome
from dental_image_organizer.storage.library import LibraryListing, PatientEntry
from dental_image_organizer.storage.schema import (
    GAP_COLOR_PATTERN,
    GAP_PX_MAX,
    CaseRecord,
    CaseType,
    CellSize,
    Id,
    PatientRecord,
    PhotoState,
    Stage,
)


class ErrorBody(BaseModel):
    code: str
    message: str
    details: Any | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class Health(BaseModel):
    status: Literal["ok"]
    version: str


class Stopping(BaseModel):
    status: Literal["stopping"]


class Updating(BaseModel):
    status: Literal["updating"]


class UpdateFailureView(BaseModel):
    message: str
    log_file: str


class UpdateStatus(BaseModel):
    """The version check's answer and whether "Update now" can run here (spec §7.3)."""

    current: str
    # None until a check succeeded, or while the check is turned off.
    latest: str | None
    available: bool
    checked_at: datetime | None
    can_update: bool
    # Why `can_update` is false; the page then shows `command` for Terminal.
    unavailable_reason: str | None
    command: str
    last_failure: UpdateFailureView | None

    @classmethod
    def of(cls, info: UpdateInfo) -> UpdateStatus:
        failure = (
            None
            if info.failure is None
            else UpdateFailureView(message=info.failure, log_file=str(info.log_file))
        )
        return cls(
            current=info.current,
            latest=info.latest,
            available=info.available,
            checked_at=info.checked_at,
            can_update=info.blocker is None,
            unavailable_reason=info.blocker,
            command=info.command,
            last_failure=failure,
        )


class RevealRequest(BaseModel):
    path: str


class JobRef(BaseModel):
    job_id: str | None


class JobView(BaseModel):
    id: str
    kind: str
    state: JobState
    done: int
    total: int
    message: str | None
    result: dict[str, Any] | None
    error: ErrorBody | None

    @classmethod
    def of(cls, snapshot: JobSnapshot) -> JobView:
        failure = snapshot.error
        return cls(
            id=snapshot.id,
            kind=snapshot.kind,
            state=snapshot.state,
            done=snapshot.done,
            total=snapshot.total,
            message=snapshot.message,
            result=dict(snapshot.result) if snapshot.result is not None else None,
            error=ErrorBody(code=failure.code, message=failure.message) if failure else None,
        )


class _Request(BaseModel):
    # A misspelled field would otherwise be dropped silently, and the change with it.
    model_config = ConfigDict(extra="forbid")


# -- library -------------------------------------------------------------------------------


class CaseSummary(BaseModel):
    id: str
    type: CaseType
    title: str
    # Relative to the library root, `/`-separated, as on disk: what /system/reveal takes.
    folder: str
    created_at: datetime
    updated_at: datetime
    photos: int
    needs_review: int
    pending: int
    failed: int
    thumbnails: list[str]

    @classmethod
    def of(cls, case: CaseRecord, folder: str) -> CaseSummary:
        states = Counter(p.state for p in case.photos)
        return cls(
            id=case.id,
            type=case.type,
            title=case.title,
            folder=folder,
            created_at=case.created_at,
            updated_at=case.updated_at,
            photos=len(case.photos),
            needs_review=sum(
                p.state == "ready" and p.edit is not None and p.edit.status == "needs_review"
                for p in case.photos
            ),
            pending=states["pending"],
            failed=states["failed"],
            thumbnails=cases.thumbnails(case),
        )


class PatientView(BaseModel):
    id: str
    name: str
    note: str | None
    folder: str  # as CaseSummary.folder
    created_at: datetime
    cases: list[CaseSummary]

    @classmethod
    def of(cls, entry: PatientEntry) -> PatientView:
        patient = entry.record
        # Patient folders sit right under the root, so their name is the relative path.
        folder = entry.folder.name
        return cls(
            id=patient.id,
            name=patient.name,
            note=patient.note,
            folder=folder,
            created_at=patient.created_at,
            cases=[
                CaseSummary.of(case, f"{folder}/{entry.case_folders[case.id].name}")
                for case in entry.cases
            ],
        )


class LibraryProblemView(BaseModel):
    folder: str
    message: str


class PatientsView(BaseModel):
    patients: list[PatientView]
    problems: list[LibraryProblemView]

    @classmethod
    def of(cls, listing: LibraryListing) -> PatientsView:
        return cls(
            patients=[PatientView.of(entry) for entry in listing.patients],
            problems=[
                LibraryProblemView(folder=p.folder, message=p.message) for p in listing.problems
            ],
        )


class PatientPatch(_Request):
    """Only the fields sent change; `"note": null` clears the note."""

    name: str | None = None
    note: str | None = None

    @model_validator(mode="after")
    def _keeps_a_name(self) -> Self:
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("a patient's name cannot be removed")
        return self


# -- cases ---------------------------------------------------------------------------------


class CaseCreate(_Request):
    """`patient_id` for an existing patient, or `patient_name` (and a note) for a new one."""

    type: CaseType
    title: str
    patient_id: str | None = None
    patient_name: str | None = None
    patient_note: str | None = None


class CaseView(BaseModel):
    case: CaseRecord
    patient: PatientRecord
    folder: str  # as CaseSummary.folder; its export files are in `{folder}/export`
    suggested_output_scale: float | None
    flip_defaults: dict[Slot, Flip] | None

    @classmethod
    def of(cls, details: CaseDetails) -> CaseView:
        return cls(
            case=details.case,
            patient=details.patient,
            folder=details.folder,
            suggested_output_scale=details.suggested_output_scale,
            flip_defaults=details.flip_defaults,
        )


class SequencePatch(_Request):
    # The bounds of case.json, so a value the record would refuse is refused as a request.
    photo_ids: list[Id]
    gap_px: int = Field(default=0, ge=0, le=GAP_PX_MAX)


class CompositePatch(_Request):
    """Merged into the case's layout: only the fields sent change, and
    `"baseline_photo_id": null` returns the baseline to the default slot."""

    baseline_photo_id: Id | None = None
    cell_size: CellSize | None = None
    gap_px: Annotated[int, Field(ge=0, le=GAP_PX_MAX)] | None = None
    gap_color: Annotated[str, Field(pattern=GAP_COLOR_PATTERN)] | None = None

    @model_validator(mode="after")
    def _only_the_baseline_resets(self) -> Self:
        nulled = [
            name
            for name in ("cell_size", "gap_px", "gap_color")
            if name in self.model_fields_set and getattr(self, name) is None
        ]
        if nulled:
            raise ValueError(f"{', '.join(nulled)} cannot be null")
        return self


class CasePatch(_Request):
    title: str | None = None
    output_scale: float | None = None
    sequence: SequencePatch | None = None
    composite: CompositePatch | None = None


# -- photos --------------------------------------------------------------------------------


class ManualEdit(_Request):
    transform: list[float]
    model: AlignModel


class PhotoPatch(_Request):
    """Exactly one change per request."""

    stage: Stage | None = None
    slot: Slot | Literal["tray"] | None = None
    flip: Flip | None = None
    edit: ManualEdit | None = None
    reset: Literal[True] | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> Self:
        sent = self.model_fields_set
        if len(sent) != 1 or getattr(self, next(iter(sent))) is None:
            raise ValueError("send exactly one change")
        return self


class SlotMap(_Request):
    slots: dict[Slot, str]


class SkippedView(BaseModel):
    name: str
    reason: UploadSkipReason
    message: str


class UploadResult(BaseModel):
    job_id: str | None
    added: list[str]
    skipped: list[SkippedView]

    @classmethod
    def of(cls, outcome: UploadOutcome) -> UploadResult:
        return cls(
            job_id=outcome.job_id,
            added=list(outcome.added),
            skipped=[
                SkippedView(name=s.name, reason=s.reason, message=s.message)
                for s in outcome.skipped
            ],
        )


# -- export and archives -------------------------------------------------------------------


class ExportRequest(_Request):
    # Keeping both never replaces anything, so it is the default.
    include: list[ExportInclude] | None = None
    conflict: ConflictMode = "keep_both"


class ImportResult(BaseModel):
    status: Literal["imported", "skipped"]
    case_id: str
    patient_id: str
    title: str

    @classmethod
    def of(cls, outcome: ImportOutcome) -> ImportResult:
        return cls(
            status=outcome.status,
            case_id=outcome.case_id,
            patient_id=outcome.patient_id,
            title=outcome.title,
        )


# -- AI model, classifier, and teaching ------------------------------------------------------


class ModelStatus(BaseModel):
    available: bool
    size_bytes: int
    # Where the download comes from, so the consent text can name it (spec §4.6).
    host: str
    download_job_id: str | None


class FlipStatusView(BaseModel):
    effective: Flip
    start: Flip
    learned: Flip | None
    pinned: bool
    confirmations: int

    @classmethod
    def of(cls, status: FlipStatus) -> FlipStatusView:
        return cls(
            effective=status.effective,
            start=status.start,
            learned=status.learned,
            pinned=status.pinned,
            confirmations=status.confirmations,
        )


class ClassifierView(BaseModel):
    trained: bool
    min_samples_per_slot: int
    counts: dict[Slot, int]
    stale_samples: int
    damaged: str | None
    model_available: bool
    flips: dict[Slot, FlipStatusView]
    # Why flips.json could not be read; `flips` then shows the pins and start values in use.
    flips_damaged: str | None

    @classmethod
    def of(
        cls,
        status: ClassifierStatus,
        flips: dict[Slot, FlipStatus],
        *,
        flips_damaged: str | None,
        min_samples: int,
    ) -> ClassifierView:
        return cls(
            trained=status.trained,
            min_samples_per_slot=min_samples,
            counts=status.counts,
            stale_samples=status.stale_samples,
            damaged=status.damaged,
            model_available=status.model_available,
            flips={slot: FlipStatusView.of(flip) for slot, flip in flips.items()},
            flips_damaged=flips_damaged,
        )


class TeachPhotoView(BaseModel):
    id: str
    name: str
    captured_at: datetime | None
    state: PhotoState
    error: str | None


class TeachSessionView(BaseModel):
    id: str
    state: SessionState
    photos: list[TeachPhotoView]
    proposal: dict[Slot, str]
    job_id: str

    @classmethod
    def of(cls, session: TeachSession) -> TeachSessionView:
        return cls(
            id=session.id,
            state=session.state,
            photos=[
                TeachPhotoView(
                    id=p.id, name=p.name, captured_at=p.captured_at, state=p.state, error=p.error
                )
                for p in session.photos
            ],
            proposal=dict(session.proposal),
            job_id=session.job_id,
        )


class TeachUpload(BaseModel):
    # Both are null when every file was skipped: no session was started.
    session_id: str | None
    job_id: str | None
    skipped: list[SkippedView]


class TeachConfirm(_Request):
    slots: dict[Slot, str]
    flips: dict[Slot, Flip] = Field(default_factory=dict)
