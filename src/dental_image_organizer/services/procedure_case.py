"""Feature 1 in the library: reference, alignment, stages, and manual edits (spec §3.2, §4.8).

Decoding, aligning, and fitting colors take up to seconds per photo, so they run outside the
case lock. Each result is then applied in its own `update_case` to the case as it is at that
moment: a photo deleted meanwhile is skipped, and a manual edit made meanwhile is kept.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

import numpy as np
import structlog
from structlog.contextvars import bound_contextvars

from dental_image_organizer.core import transform as tf
from dental_image_organizer.core.align import AlignModel
from dental_image_organizer.core.color import LabCurves
from dental_image_organizer.core.imageio import DecodedImage
from dental_image_organizer.errors import (
    CorruptImageError,
    InvalidEditError,
    InvalidRequestError,
    NotReadyError,
    OutputTooLargeError,
)
from dental_image_organizer.services.batch_io import os_error_message, unexpected_message
from dental_image_organizer.services.intake import (
    Skipped,
    Upload,
    UploadOutcome,
    add_photos,
    pending_photo,
    stage_upload,
)
from dental_image_organizer.services.jobs import JobProgress, JobSnapshot
from dental_image_organizer.services.originals import (
    decode_original,
    remember_working,
    working_copy,
)
from dental_image_organizer.services.procedure import (
    ProcedureEdit,
    compute_edit,
    fit_colors,
    output_size,
)
from dental_image_organizer.storage.library import local_now
from dental_image_organizer.storage.schema import (
    GAP_PX_MAX,
    OUTPUT_SCALES,
    CaseRecord,
    ColorRecord,
    EditRecord,
    PhotoRecord,
    SequenceRecord,
    Stage,
)

if TYPE_CHECKING:
    from dental_image_organizer.services.context import AppContext

log = structlog.get_logger(__name__)

PROCEDURE_ALGORITHM = "align-1"
SUGGEST_AT_DETAIL_RATIO = 1.5
SEQUENCE_SIZE = (2, 10)
# The bound case.json enforces, so a request is refused before the record would be.
MAX_GAP_PX = GAP_PX_MAX
MIN_DETERMINANT = 1e-12
SIMILARITY_ROW_TOLERANCE = 1e-9

_COMPOSITE_MESSAGE = "This is a composite case."
_NO_REFERENCE_MESSAGE = "Set the reference first."
_REPLACED_MESSAGE = "The reference was replaced meanwhile; align the photo again."


@dataclass(frozen=True)
class _Reference:
    photo: PhotoRecord
    size: tuple[int, int]  # of the decoded original


# -- uploads -------------------------------------------------------------------------------


def upload(ctx: AppContext, case_id: str, uploads: Sequence[Upload]) -> UploadOutcome:
    _read(ctx, case_id)
    added, skipped = add_photos(ctx, case_id, uploads, role="photo")
    job = _submit(ctx, "process", case_id, added) if added else None
    return UploadOutcome(job.id if job else None, tuple(added), tuple(skipped))


def set_reference(ctx: AppContext, case_id: str, upload: Upload) -> UploadOutcome:
    """Make the upload the case's reference and re-process every photo against it (§3.2 step 2)."""
    with bound_contextvars(case_id=case_id):
        library = ctx.library()
        _read(ctx, case_id)
        directory = library.originals_dir(case_id)
        # Staged outside the case lock, as add_photos does: hashing a large file must not block
        # the case's other updates.
        staged = stage_upload(upload, directory)
        if isinstance(staged, Skipped):
            return UploadOutcome(None, (), (staged,))
        chosen: list[PhotoRecord] = []
        created: list[PhotoRecord] = []
        removed: list[PhotoRecord] = []

        def change(case: CaseRecord) -> CaseRecord:
            chosen.clear()
            created.clear()
            removed.clear()
            previous = case.reference()
            existing = next((p for p in case.photos if p.sha256 == staged.sha256), None)
            if existing is None:
                base = pending_photo(staged, "reference", local_now(), directory)
            else:
                # Pending until its job checks it as a reference: its size at the case's
                # output scale may be too large, and nothing may align to it before that.
                base = existing.model_copy(
                    update={"role": "reference", "state": "pending", "error": None}
                )
            reference = base.model_copy(
                update={"stage": "pre_op", "stage_source": "auto", "edit": None}
            )
            chosen.append(reference)
            if existing is None:
                created.append(reference)
            if previous is not None and previous.id != reference.id:
                removed.append(previous)
            gone = {p.id for p in removed}
            # Manual edits were made against the old reference frame, so they no longer apply.
            photos = [
                reference if p.id == reference.id else p.model_copy(update={"edit": None})
                for p in case.photos
                if p.id not in gone
            ]
            photos.extend(created)
            sequence = case.sequence
            if sequence is not None and gone:
                kept = [i for i in sequence.photo_ids if i not in gone]
                sequence = sequence.model_copy(update={"photo_ids": kept})
            return _restaged(case.model_copy(update={"photos": photos, "sequence": sequence}))

        updated = library.update_case(case_id, change)
        for photo in removed:
            _remove_unused_original(ctx, case_id, photo)
        reference = chosen[0]
        others = [p.id for p in updated.photos if p.id != reference.id]
        job = _submit(ctx, "reference", case_id, [reference.id, *others])
        log.info("reference_set", photo_id=reference.id, replaced=bool(removed))
        return UploadOutcome(job.id, tuple(p.id for p in created), ())


def _remove_unused_original(ctx: AppContext, case_id: str, photo: PhotoRecord) -> None:
    library = ctx.library()
    # Decided under the case lock after the record is gone, never inside the change that
    # removed it: a write that failed would otherwise lose a file a record still uses. Uploads
    # record a staged file under the same lock, so one with these bytes is seen here or finds
    # its file missing.
    with ctx.case_locks.hold(case_id):
        if any(p.file == photo.file for p in library.read_case(case_id).photos):
            return
        try:
            library.original_path(case_id, photo).unlink(missing_ok=True)
        except OSError as exc:
            # The reference is replaced already; a file left behind only costs disk space.
            log.warning(
                "original_remove_failed",
                photo_id=photo.id,
                error=exc.strerror or type(exc).__name__,
            )


def _submit(ctx: AppContext, kind: str, case_id: str, photo_ids: Sequence[str]) -> JobSnapshot:
    ids = tuple(photo_ids)

    def run(progress: JobProgress) -> dict[str, Any]:
        return process_photos(ctx, case_id, ids, progress)

    return ctx.jobs.submit(kind, run, key=case_id, total=len(ids))


# -- processing ----------------------------------------------------------------------------


def process_photos(
    ctx: AppContext, case_id: str, photo_ids: Sequence[str], progress: JobProgress
) -> dict[str, Any]:
    """The body of the `process` and `reference` jobs (§3.2 step 3); one bad photo fails alone."""
    with bound_contextvars(case_id=case_id):
        wanted = list(dict.fromkeys(photo_ids))
        reference = _read(ctx, case_id).reference()
        processed: list[PhotoRecord] = []
        done = 0
        # First, so that the other photos of this run can be aligned to it.
        if reference is not None and (reference.id in wanted or reference.state == "pending"):
            with bound_contextvars(photo_id=reference.id):
                result = _process_reference(ctx, case_id, reference)
            if result is not None:
                processed.append(result)
            if reference.id in wanted:
                done += 1
                progress(done, len(wanted))
        for photo_id in wanted:
            if reference is not None and photo_id == reference.id:
                continue
            with bound_contextvars(photo_id=photo_id):
                result = _process_photo(ctx, case_id, photo_id)
            if result is not None:
                processed.append(result)
            done += 1
            progress(done, len(wanted))
        log.info(
            "photos_processed",
            processed=len(processed),
            failed=sum(p.state == "failed" for p in processed),
        )
        return {"photos": [_outcome(p) for p in processed]}


def _process_reference(ctx: AppContext, case_id: str, reference: PhotoRecord) -> PhotoRecord | None:
    decoded, error = _decode(ctx, case_id, reference)

    def change(case: CaseRecord) -> CaseRecord:
        current = _find(case, reference.id)
        if current is None or current.role != "reference":
            return case  # replaced meanwhile; the new reference's job handles the case
        if decoded is None:
            return _restaged(case.replace_photo(_failed(current, error)))
        updated = _with_metadata(current, decoded)
        try:
            size = output_size(decoded.size, _scale(case))
        except OutputTooLargeError as exc:
            log.warning("reference_too_large", error=str(exc))
            updated = _failed(updated, str(exc))
        else:
            ready = {"state": "ready", "error": None, "edit": reference_edit(size)}
            updated = updated.model_copy(update=ready)
        return _restaged(case.replace_photo(updated))

    return _find(ctx.library().update_case(case_id, change), reference.id)


def _process_photo(ctx: AppContext, case_id: str, photo_id: str) -> PhotoRecord | None:
    case = _read(ctx, case_id)
    photo = _find(case, photo_id)
    # Deleted meanwhile, or made the reference, which its own job processes.
    if photo is None or photo.role == "reference":
        return None
    decoded, error = _decode(ctx, case_id, photo)
    reference = _ready_reference(case)
    computed: ProcedureEdit | None = None
    if decoded is not None and reference is not None and not _is_manual(photo):
        computed, error = _align(ctx, case_id, decoded, reference.photo)

    def change(case: CaseRecord) -> CaseRecord:
        current = _find(case, photo_id)
        if current is None or current.role == "reference":
            return case
        updated = current if decoded is None else _with_metadata(current, decoded)
        # Manual edits are never overwritten by automatic processing (spec §4.8).
        manual = _is_manual(current)
        if error is not None:
            updated = _failed(updated, error, keep=current.edit if manual else None)
        else:
            edit = current.edit
            now = _ready_reference(case)
            # An alignment to a reference replaced meanwhile is dropped; that reference's job
            # aligns the photo again.
            if computed is not None and not manual and now is not None and _same(now, reference):
                edit = edit_from_alignment(computed, output_size(now.size, _scale(case)))
            ready = {"state": "ready", "error": None, "edit": edit}
            updated = updated.model_copy(update=ready)
        return _restaged(case.replace_photo(updated))

    return _find(ctx.library().update_case(case_id, change), photo_id)


def _decode(
    ctx: AppContext, case_id: str, photo: PhotoRecord
) -> tuple[DecodedImage | None, str | None]:
    """The decoded original, with its working copy cached; or the failure message (§4.13)."""
    try:
        decoded = decode_original(ctx, case_id, photo)
        remember_working(ctx, photo.sha256, decoded.rgb)
    except CorruptImageError as exc:
        log.error("photo_unreadable", error=str(exc))
        return None, str(exc)
    except OSError as exc:
        log.error("photo_unreadable", error=os_error_message(exc))
        return None, os_error_message(exc)
    except Exception as exc:
        # Decoders parse untrusted bytes and hold several full-size copies, so a plugin error
        # or a MemoryError here must not abort the batch either.
        log.exception("photo_unreadable")
        return None, unexpected_message(exc)
    return decoded, None


def _align(
    ctx: AppContext, case_id: str, decoded: DecodedImage, reference: PhotoRecord
) -> tuple[ProcedureEdit | None, str | None]:
    try:
        return compute_edit(decoded.rgb, ctx.references.get(ctx, case_id, reference)), None
    except OSError as exc:
        log.error("photo_failed", error=os_error_message(exc))
        return None, os_error_message(exc)
    except Exception as exc:
        # One bad photo never aborts a batch: an OpenCV assertion or a MemoryError fails only
        # this photo. The trace is logged for fixing the cause.
        log.exception("photo_failed")
        return None, unexpected_message(exc)


def _with_metadata(photo: PhotoRecord, decoded: DecodedImage) -> PhotoRecord:
    width, height = decoded.size
    update: dict[str, Any] = {"width": width, "height": height}
    # Without an EXIF time, the provisional time from the upload stays (spec §4.4 step 5).
    if decoded.metadata.captured_at is not None:
        update |= {"captured_at": decoded.metadata.captured_at, "captured_at_source": "exif"}
    return photo.model_copy(update=update)


def _failed(photo: PhotoRecord, error: str | None, keep: EditRecord | None = None) -> PhotoRecord:
    return photo.model_copy(update={"state": "failed", "error": error, "edit": keep})


def _outcome(photo: PhotoRecord) -> dict[str, Any]:
    return {
        "photo_id": photo.id,
        "state": photo.state,
        "status": photo.edit.status if photo.edit else None,
        "error": photo.error,
    }


# -- edits ---------------------------------------------------------------------------------


def apply_manual_edit(
    ctx: AppContext,
    case_id: str,
    photo_id: str,
    transform: Sequence[float],
    model: AlignModel,
) -> CaseRecord:
    with bound_contextvars(case_id=case_id, photo_id=photo_id):
        case = _read(ctx, case_id)
        photo, reference = _editable(case, photo_id)
        matrix = validate_transform(transform, model, output_size(reference.size, _scale(case)))
        # Fitted before the case lock is taken: the fit warps and converts whole working
        # copies, and the case's other changes must not wait for it.
        curves = fit_colors(
            working_copy(ctx, case_id, photo),
            matrix,
            ctx.references.get(ctx, case_id, reference.photo),
        )

        def change(case: CaseRecord) -> CaseRecord:
            current, now = _editable(case, photo_id)
            if not _same(now, reference):
                raise NotReadyError(_REPLACED_MESSAGE)
            automatic = current.edit
            edit = EditRecord(
                transform=_flat(matrix),
                model=model,
                output_size=output_size(now.size, _scale(case)),
                detail_ratio=automatic.detail_ratio if automatic else None,
                color=_color(curves),
                source="manual",
                confidence=automatic.confidence if automatic else None,
                status="ok",
                message=None,
                algorithm_version=PROCEDURE_ALGORITHM,
            )
            return case.replace_photo(current.model_copy(update={"edit": edit}))

        return ctx.library().update_case(case_id, change)


def reset_edit(ctx: AppContext, case_id: str, photo_id: str) -> CaseRecord:
    """Replace the photo's edit with a fresh automatic alignment ("Reset to auto", §4.8)."""
    with bound_contextvars(case_id=case_id, photo_id=photo_id):
        case = _read(ctx, case_id)
        if case.photo(photo_id).role == "reference":
            raise InvalidRequestError("The reference is not aligned, so it has no edit to reset.")
        photo, reference = _editable(case, photo_id)
        computed = compute_edit(
            decode_original(ctx, case_id, photo).rgb,
            ctx.references.get(ctx, case_id, reference.photo),
        )

        def change(case: CaseRecord) -> CaseRecord:
            current, now = _editable(case, photo_id)
            if not _same(now, reference):
                raise NotReadyError(_REPLACED_MESSAGE)
            edit = edit_from_alignment(computed, output_size(now.size, _scale(case)))
            return case.replace_photo(current.model_copy(update={"edit": edit}))

        return ctx.library().update_case(case_id, change)


def _editable(case: CaseRecord, photo_id: str) -> tuple[PhotoRecord, _Reference]:
    photo = case.photo(photo_id)
    reference = _ready_reference(case)
    if photo.role == "reference" or photo.state != "ready" or reference is None:
        raise NotReadyError(_NO_REFERENCE_MESSAGE)
    return photo, reference


def validate_transform(
    values: Sequence[float], model: AlignModel, output_size: tuple[int, int]
) -> tf.Matrix:
    """The matrix of a manual edit; InvalidEditError when it cannot place a photo in the frame."""
    if len(values) != 9:
        raise InvalidEditError(f"A transform has 9 numbers, not {len(values)}.")
    matrix = np.asarray(values, dtype=np.float64).reshape(3, 3)
    if not np.isfinite(matrix).all():
        raise InvalidEditError("Every number of a transform must be finite.")
    if abs(float(np.linalg.det(matrix))) < MIN_DETERMINANT:
        raise InvalidEditError("The transform cannot be inverted.")
    if model == "similarity" and not np.allclose(
        matrix[2], (0.0, 0.0, 1.0), rtol=0.0, atol=SIMILARITY_ROW_TOLERANCE
    ):
        raise InvalidEditError("A similarity transform ends with the row [0, 0, 1].")
    width, height = output_size
    aspect = height / width
    frame = np.array([[0.0, 0.0, 1.0], [1.0, 0.0, 1.0], [1.0, aspect, 1.0], [0.0, aspect, 1.0]])
    # The photo points the output corners come from; a corner at w <= 0 has no such point.
    mapped = frame @ np.linalg.inv(matrix).T
    if (mapped[:, 2] <= 0.0).any():
        raise InvalidEditError("The transform puts part of the output frame behind the camera.")
    corners = mapped[:, :2] / mapped[:, 2:]
    edges = np.roll(corners, -1, axis=0) - corners
    following = np.roll(edges, -1, axis=0)
    turns = edges[:, 0] * following[:, 1] - edges[:, 1] * following[:, 0]
    if not ((turns > 0.0).all() or (turns < 0.0).all()):
        raise InvalidEditError("The transform folds the output frame over itself.")
    return matrix


def edit_from_alignment(edit: ProcedureEdit, size: tuple[int, int]) -> EditRecord:
    align = edit.align
    return EditRecord(
        transform=_flat(edit.transform),
        model=align.model,
        output_size=size,
        detail_ratio=align.detail_ratio,
        color=_color(edit.curves),
        source="auto",
        confidence=align.confidence,
        status=align.status,
        message=align.message,
        algorithm_version=PROCEDURE_ALGORITHM,
    )


def reference_edit(size: tuple[int, int]) -> EditRecord:
    return EditRecord(
        transform=_flat(tf.identity()),
        model="similarity",
        output_size=size,
        detail_ratio=1.0,
        color=ColorRecord(method="identity"),
        source="auto",
        confidence=1.0,
        status="ok",
        message=None,
        algorithm_version=PROCEDURE_ALGORITHM,
    )


def _flat(matrix: tf.Matrix) -> list[float]:
    return [float(v) for v in matrix.ravel()]


def _color(curves: LabCurves | None) -> ColorRecord:
    if curves is None:
        return ColorRecord(method="identity")
    return ColorRecord(method="lab_quantile_v1", params=curves.to_params())


# -- stages, output scale, and sequence ----------------------------------------------------


def set_stage(ctx: AppContext, case_id: str, photo_id: str, stage: Stage) -> CaseRecord:
    def change(case: CaseRecord) -> CaseRecord:
        photo = _procedure(case).photo(photo_id)
        manual = {"stage": stage, "stage_source": "manual"}
        return case.replace_photo(photo.model_copy(update=manual))

    with bound_contextvars(case_id=case_id):
        return ctx.library().update_case(case_id, change)


def set_output_scale(ctx: AppContext, case_id: str, scale: float) -> CaseRecord:
    def change(case: CaseRecord) -> CaseRecord:
        _procedure(case)
        if scale not in OUTPUT_SCALES:
            allowed = ", ".join(f"{s:g}" for s in OUTPUT_SCALES)
            raise InvalidRequestError(f"The output scale must be one of {allowed}.")
        photos = case.photos
        reference = _ready_reference(case)
        if reference is not None:
            # Raises OutputTooLargeError before anything is written, so nothing changes then.
            size = output_size(reference.size, scale)
            # Transforms are normalized by the output width, so they hold at any scale (§4.8).
            photos = [
                p.model_copy(update={"edit": p.edit.model_copy(update={"output_size": size})})
                if p.edit is not None
                else p
                for p in case.photos
            ]
        return case.model_copy(update={"output_scale": scale, "photos": photos})

    with bound_contextvars(case_id=case_id):
        return ctx.library().update_case(case_id, change)


def set_sequence(
    ctx: AppContext, case_id: str, photo_ids: Sequence[str], gap_px: int
) -> CaseRecord:
    ids = list(photo_ids)

    def change(case: CaseRecord) -> CaseRecord:
        _procedure(case)
        if not 0 <= gap_px <= MAX_GAP_PX:
            raise InvalidRequestError(f"The gap must be 0 to {MAX_GAP_PX} px.")
        if ids:
            low, high = SEQUENCE_SIZE
            if not low <= len(ids) <= high or len(set(ids)) != len(ids):
                raise InvalidRequestError(f"Select {low} to {high} different photos.")
            if not set(ids) <= {p.id for p in _usable(case)}:
                raise InvalidRequestError("Only processed photos can be in the sequence.")
        sequence = SequenceRecord(photo_ids=ids, gap_px=gap_px)
        return case.model_copy(update={"sequence": sequence})

    with bound_contextvars(case_id=case_id):
        return ctx.library().update_case(case_id, change)


def auto_stages(case: CaseRecord) -> dict[str, Stage | None]:
    """The stage rule of §3.2 step 5 for every photo, whether its stage is automatic or not."""
    # The wall date as recorded, like the export file names (§4.9): an offset is not applied.
    days = {
        p.id: p.captured_at.date()
        for p in case.photos
        if p.role != "reference" and p.state == "ready" and p.captured_at is not None
    }
    first_day = min(days.values(), default=None)
    stages: dict[str, Stage | None] = {}
    for photo in case.photos:
        day = days.get(photo.id)
        if photo.role == "reference":
            stages[photo.id] = "pre_op"
        elif day is None:
            stages[photo.id] = None
        else:
            stages[photo.id] = "intra_op" if day == first_day else "post_op"
    return stages


def _restaged(case: CaseRecord) -> CaseRecord:
    stages = auto_stages(case)
    photos = [
        p.model_copy(update={"stage": stages[p.id]}) if p.stage_source == "auto" else p
        for p in case.photos
    ]
    return case.model_copy(update={"photos": photos})


def suggested_output_scale(case: CaseRecord) -> float | None:
    """A larger output scale when the photos carry more detail than the reference (§3.2 step 2)."""
    reference = _ready_reference(case)
    if reference is None:
        return None
    ratios = [
        p.edit.detail_ratio
        for p in case.photos
        if p.role != "reference" and p.edit is not None and p.edit.detail_ratio is not None
    ]
    if not ratios:
        return None
    median = statistics.median(ratios)
    if median < SUGGEST_AT_DETAIL_RATIO:
        return None
    for scale in sorted(OUTPUT_SCALES, reverse=True):
        if not _scale(case) < scale <= median:
            continue
        try:
            output_size(reference.size, scale)
        except OutputTooLargeError:
            continue
        return scale
    return None


def sequence_photos(case: CaseRecord) -> list[PhotoRecord]:
    """The frames of the sequence image, in order (§3.2 step 6)."""
    usable = {p.id: p for p in _usable(case)}
    chosen = case.sequence.photo_ids if case.sequence is not None else []
    if chosen:
        return [usable[i] for i in chosen if i in usable]
    return sorted(usable.values(), key=_capture_order)


def _capture_order(photo: PhotoRecord) -> tuple[datetime, str]:
    # The wall-clock time as recorded orders one case: EXIF times that carry an offset and
    # upload times must still compare with naive EXIF times.
    when = photo.captured_at.replace(tzinfo=None) if photo.captured_at else datetime.max
    return when, photo.id


def _usable(case: CaseRecord) -> list[PhotoRecord]:
    return [p for p in case.photos if p.state == "ready" and p.edit is not None]


# -- records -------------------------------------------------------------------------------


def _read(ctx: AppContext, case_id: str) -> CaseRecord:
    return _procedure(ctx.library().read_case(case_id))


def _procedure(case: CaseRecord) -> CaseRecord:
    if case.type != "procedure":
        raise InvalidRequestError(_COMPOSITE_MESSAGE)
    return case


def _scale(case: CaseRecord) -> float:
    # The schema gives every procedure case one of OUTPUT_SCALES on each load and write.
    assert case.output_scale is not None
    return case.output_scale


def _find(case: CaseRecord, photo_id: str) -> PhotoRecord | None:
    return next((p for p in case.photos if p.id == photo_id), None)


def _is_manual(photo: PhotoRecord) -> bool:
    return photo.edit is not None and photo.edit.source == "manual"


def _ready_reference(case: CaseRecord) -> _Reference | None:
    photo = case.reference()
    if photo is None or photo.state != "ready" or photo.width is None or photo.height is None:
        return None
    return _Reference(photo, (photo.width, photo.height))


def _same(now: _Reference | None, then: _Reference | None) -> bool:
    if now is None or then is None:
        return False
    return (now.photo.id, now.photo.sha256) == (then.photo.id, then.photo.sha256)
