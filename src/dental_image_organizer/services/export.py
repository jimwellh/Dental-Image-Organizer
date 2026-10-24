"""Case and patient export into each case's `export/` folder, and the download zips (spec §3.2
step 7, §3.3 steps 6-7, §3.4, §4.9).

Pixels are rendered from a snapshot of the case read without a lock, and each file goes to a
hidden part file as soon as it is encoded, so encoded JPEGs never pile up in memory. Only naming,
moving the parts into place, and recording them in `case.exports` hold the case lock. A file the
app did not write is never deleted or overwritten: "Replace previous export" removes only what
`case.exports` records (spec amendment 4).
"""

from __future__ import annotations

import os
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

import numpy as np
import structlog
from structlog.contextvars import bound_contextvars

from dental_image_organizer.config import Settings
from dental_image_organizer.core.imageio import Metadata, RGBImage, encode_jpeg
from dental_image_organizer.core.naming import (
    case_export_zip_name,
    composite_capture_date,
    composite_filename,
    patient_export_zip_name,
    photo_filename,
    sequence_filename,
    unique_name,
)
from dental_image_organizer.core.slots import Flip, Slot
from dental_image_organizer.errors import (
    CaseNotFoundError,
    DioError,
    InvalidRequestError,
    JobNotFoundError,
    NotReadyError,
    PathOutsideLibraryError,
    StorageError,
)
from dental_image_organizer.ml.embedder import EmbeddingPair
from dental_image_organizer.services.batch_io import failure_message, os_error_message
from dental_image_organizer.services.jobs import JobProgress
from dental_image_organizer.services.originals import decode_original, photo_embedding
from dental_image_organizer.services.renders import composite_grid, render_full, sequence_strip
from dental_image_organizer.storage.library import EXPORT_DIR, Library, local_now
from dental_image_organizer.storage.schema import (
    CaseRecord,
    CaseType,
    EditRecord,
    ExportKind,
    ExportRecord,
    PhotoRecord,
)

if TYPE_CHECKING:
    from dental_image_organizer.services.context import AppContext

log = structlog.get_logger(__name__)

ExportInclude = Literal["singles", "sequence", "composite"]
ConflictMode = Literal["replace", "keep_both"]

# Everything that fits a case type is exported by default, so this also says what fits.
DEFAULT_INCLUDE: dict[CaseType, tuple[ExportInclude, ...]] = {
    "procedure": ("singles", "sequence"),
    "composite": ("composite",),
}

_EMPTY_MESSAGE = "Choose at least one thing to export."
_NO_CAPTURE_TIME_MESSAGE = "This photo has no capture time to name its file by."
_NOTHING_TO_RENDER: dict[ExportInclude, str] = {
    "sequence": "No processed photos for the sequence.",
    "composite": "No photo is placed yet.",
}
_NO_EXPORT_MESSAGE = "There is no finished export with this id."


@dataclass(frozen=True)
class _Source:
    """What one case export renders from: a snapshot of the case, read without a lock."""

    ctx: AppContext
    case: CaseRecord
    patient: str
    today: date
    folder: Path
    settings: Settings


@dataclass(frozen=True)
class _Part:
    """An encoded file in the export folder under a hidden name, waiting for its real one."""

    hidden: str
    name: str
    kind: ExportKind
    photo_id: str | None


# -- jobs ----------------------------------------------------------------------------------


def start_case_export(
    ctx: AppContext,
    case_id: str,
    include: Sequence[ExportInclude] | None,
    conflict: ConflictMode,
) -> str:
    """Queue the export of one case; the job id.

    The job shares the case's key, so it runs after the processing jobs queued before it and
    exports the photos they process.
    """
    # Refused here, so a request that cannot fit the case never becomes a failed job.
    chosen = _included(ctx.library().read_case(case_id), include)

    def run(progress: JobProgress) -> dict[str, Any]:
        return export_case(ctx, case_id, chosen, conflict)

    return ctx.jobs.submit("export", run, key=case_id).id


def start_patient_export(
    ctx: AppContext,
    patient_id: str,
    include: Sequence[ExportInclude] | None,
    conflict: ConflictMode,
) -> str:
    """Queue the export of every case of a patient (§3.4); the job id."""
    ctx.library().patient(patient_id)
    if include is not None and not include:
        raise InvalidRequestError(_EMPTY_MESSAGE)
    wanted = tuple(include) if include is not None else None

    def run(progress: JobProgress) -> dict[str, Any]:
        return _export_patient(ctx, patient_id, wanted, conflict, progress)

    return ctx.jobs.submit("export_patient", run, key=f"patient:{patient_id}").id


def _export_patient(
    ctx: AppContext,
    patient_id: str,
    include: Sequence[ExportInclude] | None,
    conflict: ConflictMode,
    progress: JobProgress,
) -> dict[str, Any]:
    with bound_contextvars(patient_id=patient_id):
        entry = ctx.library().patient(patient_id)
        # One generation date for every case, even when the run crosses midnight.
        today = local_now().date()
        # A case that none of the chosen values fits has nothing to export and is left out.
        todo = [(c.id, fit) for c in entry.cases if (fit := _fitting(c.type, include))]
        cases: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        progress(0, len(todo))
        for done, (case_id, fit) in enumerate(todo, start=1):
            with bound_contextvars(case_id=case_id):
                try:
                    cases.append(export_case(ctx, case_id, fit, conflict, today=today))
                except Exception as exc:
                    # One case that fails never stops the others (§3.4 step 4).
                    message = _reported("case_export_failed", exc)
                    failed.append({"case_id": case_id, "message": message})
            progress(done, len(todo))
        log.info("patient_exported", cases=len(cases), failed=len(failed))
        return {"patient_id": patient_id, "cases": cases, "failed_cases": failed}


def _included(
    case: CaseRecord, include: Sequence[ExportInclude] | None
) -> tuple[ExportInclude, ...]:
    allowed = DEFAULT_INCLUDE[case.type]
    if include is None:
        return allowed
    if not include:
        raise InvalidRequestError(_EMPTY_MESSAGE)
    wrong = [value for value in dict.fromkeys(include) if value not in allowed]
    if wrong:
        raise InvalidRequestError(f"A {case.type} case has no {' or '.join(wrong)} to export.")
    return _fitting(case.type, include)


def _fitting(
    case_type: CaseType, include: Sequence[ExportInclude] | None
) -> tuple[ExportInclude, ...]:
    allowed = DEFAULT_INCLUDE[case_type]
    return allowed if include is None else tuple(v for v in allowed if v in include)


# -- one case ------------------------------------------------------------------------------


def export_case(
    ctx: AppContext,
    case_id: str,
    include: Sequence[ExportInclude] | None,
    conflict: ConflictMode,
    *,
    today: date | None = None,
) -> dict[str, Any]:
    """The body of both export jobs; a file that fails is reported and the rest export (§4.13)."""
    with bound_contextvars(case_id=case_id):
        library = ctx.library()
        case = library.read_case(case_id)
        chosen = _included(case, include)
        source = _Source(
            ctx=ctx,
            case=case,
            patient=library.patient(case.patient_id).record.name,
            today=today or local_now().date(),
            folder=library.export_dir(case_id),
            settings=ctx.settings.get(),
        )
        parts: list[_Part] = []
        failures: list[dict[str, Any]] = []
        try:
            skipped = _render(source, chosen, parts, failures)
            # With nothing new to write, a "replace" would only delete the earlier export.
            written = _commit(ctx, case_id, parts, conflict, failures) if parts else []
        finally:
            _discard_parts(library, case_id, parts)
        learning = None
        if any(record.kind == "composite" for record in written):
            # The exported composite is what the dentist confirmed, so the snapshot it was
            # rendered from is what is learned.
            learning = _learn(ctx, case, source.settings)
        log.info(
            "case_exported",
            files=len(written),
            failures=len(failures),
            pending=skipped["pending"],
            failed=skipped["failed"],
        )
        return {
            "case_id": case_id,
            "files": [record.file for record in written],
            "failures": failures,
            "skipped": skipped,
            "learning": learning,
        }


def _render(
    source: _Source,
    chosen: Sequence[ExportInclude],
    parts: list[_Part],
    failures: list[dict[str, Any]],
) -> dict[str, int]:
    """Render each chosen file into a part file, appending to `parts`; the skipped photos.

    `parts` is the caller's, so the part files written before a failure are still removed.
    """
    skipped = {"pending": 0, "failed": 0}
    for what in chosen:
        if what != "singles":
            try:
                parts.append(_sequence(source) if what == "sequence" else _composite(source))
            except NotReadyError:
                failures.append(_failure(None, _NOTHING_TO_RENDER[what]))
            except Exception as exc:
                failures.append(_failure(None, _reported("export_failed", exc, item=what)))
            continue
        for photo in source.case.photos:
            if photo.state != "ready":
                skipped[photo.state] += 1
                continue
            if photo.edit is None:
                continue  # not aligned yet: the case has no ready reference
            with bound_contextvars(photo_id=photo.id):
                try:
                    parts.append(_single(source, photo))
                except Exception as exc:
                    failures.append(_failure(photo.id, _reported("photo_export_failed", exc)))
    return skipped


def _single(source: _Source, photo: PhotoRecord) -> _Part:
    # A function of its own, so the full-size decode and render are freed before the next one.
    name = photo_filename(source.patient, source.case.title, _captured(photo))
    decoded = decode_original(source.ctx, source.case.id, photo)
    image = render_full(photo, decoded.rgb)
    return _Part(_write_part(source, image, decoded.metadata), name, "single", photo.id)


def _sequence(source: _Source) -> _Part:
    image = sequence_strip(source.ctx, source.case, full=True)
    name = sequence_filename(source.patient, source.case.title, source.today)
    return _Part(_write_part(source, image), name, "sequence", None)


def _composite(source: _Source) -> _Part:
    image = composite_grid(source.ctx, source.case, full=True)
    dates = [p.captured_at.date() for _, p, _ in _cells(source.case) if p.captured_at is not None]
    name = composite_filename(source.patient, source.case.title, composite_capture_date(dates))
    return _Part(_write_part(source, image), name, "composite", None)


def _captured(photo: PhotoRecord) -> datetime:
    # Intake always records a time; only a hand-edited case.json can lack one.
    if photo.captured_at is None:
        raise NotReadyError(_NO_CAPTURE_TIME_MESSAGE)
    return photo.captured_at


def _write_part(source: _Source, image: RGBImage, metadata: Metadata | None = None) -> str:
    settings = source.settings
    data = encode_jpeg(image, metadata, quality=settings.jpeg_quality, strip_gps=settings.strip_gps)
    # Hidden and random, so it can clash neither with the dentist's files nor with real names.
    hidden = f".{uuid4().hex}.part"
    path = source.folder / hidden
    try:
        with path.open("xb") as f:
            f.write(data)
            # Without the fsync a power loss soon after the rename can leave an empty file.
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        _discard(path)
        raise
    return hidden


def _commit(
    ctx: AppContext,
    case_id: str,
    parts: Sequence[_Part],
    conflict: ConflictMode,
    failures: list[dict[str, Any]],
) -> list[ExportRecord]:
    """Give each part its name and record it in `case.exports`, all under the case lock."""
    library = ctx.library()
    with ctx.case_locks.hold(case_id):
        # Read again under the lock: an earlier export or a rename may have changed the folder
        # and the records while this one rendered. A renamed folder took its parts along.
        case = library.read_case(case_id)
        folder = library.export_dir(case_id)
        kept = _remove_previous(library, case, folder, failures) if conflict == "replace" else None
        taken = _names_in(folder)
        now = local_now()
        written: list[ExportRecord] = []
        for part in parts:
            name = unique_name(part.name, taken)
            try:
                os.replace(folder / part.hidden, folder / name)
            except OSError as exc:
                failures.append(_failure(part.photo_id, _reported("export_move_failed", exc)))
                continue
            taken.add(name)
            written.append(
                ExportRecord(file=f"{EXPORT_DIR}/{name}", kind=part.kind, created_at=now)
            )

        def record(current: CaseRecord) -> CaseRecord:
            earlier = current.exports if kept is None else kept
            return current.model_copy(update={"exports": [*earlier, *written]})

        try:
            library.update_case(case_id, record)
        except BaseException:
            # A file left off the record could never be replaced, and would push every later
            # export of this name to _2, so the files just moved in are taken out again.
            for exported in written:
                _discard(folder / PurePosixPath(exported.file).name)
            raise
        return written


def _remove_previous(
    library: Library, case: CaseRecord, folder: Path, failures: list[dict[str, Any]]
) -> list[ExportRecord]:
    """Delete the files the case's earlier exports wrote; the records of those that stay."""
    inside = folder.resolve()
    kept: list[ExportRecord] = []
    for record in case.exports:
        try:
            path = library.resolve(folder.parent / record.file)
        except PathOutsideLibraryError:
            continue
        # A link put in place of an exported file may point at an original; only a file that
        # really is in this export folder is the app's to delete.
        if path.parent != inside or not path.is_file():
            continue
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        except OSError as exc:
            reason = exc.strerror or type(exc).__name__
            log.warning("export_remove_failed", error=reason)
            # Still on record, so the next replace tries again instead of forgetting it.
            kept.append(record)
            message = f"“{path.name}” from an earlier export could not be removed: {reason}."
            failures.append(_failure(None, message))
    return kept


def _names_in(folder: Path) -> set[str]:
    try:
        return set(os.listdir(folder))
    except OSError as exc:
        # Guessing "nothing taken" here could overwrite one of the dentist's files.
        reason = exc.strerror or type(exc).__name__
        raise StorageError(f"The export folder could not be read: {reason}") from exc


def _discard_parts(library: Library, case_id: str, parts: Sequence[_Part]) -> None:
    """Remove the part files left over; those moved into place are gone already."""
    if not parts:
        return
    try:
        folder = library.case_dir(case_id) / EXPORT_DIR
    except CaseNotFoundError:
        return  # the case went to the trash, with its folder
    for part in parts:
        _discard(folder / part.hidden)


def _discard(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        # A hidden leftover only costs disk space; the outcome of the export stands.
        log.warning("part_remove_failed", error=exc.strerror or type(exc).__name__)


# -- learning ------------------------------------------------------------------------------


def _learn(ctx: AppContext, case: CaseRecord, settings: Settings) -> dict[str, Any]:
    """Count the exported composite as confirmed (§3.3 step 7); it never fails the export.

    Runs outside the case lock: the AI lock is taken inside, and lock order puts it second.
    """
    cells = _cells(case)
    try:
        flips: dict[Slot, Flip] = {
            slot: edit.flip for slot, _, edit in cells if edit.flip is not None
        }
        ctx.ai.record_flips(case.id, flips, pins=settings.flip_pins)
        embedder = ctx.ai.embedder()
        if embedder is None:
            log.info("export_learned", flips=len(flips), samples=0)
            return {"error": "no_model", "message": None}
        pairs = [photo_embedding(ctx, case.id, photo, embedder) for _, photo, _ in cells]
        joined = EmbeddingPair(
            np.concatenate([p.original for p in pairs]),
            np.concatenate([p.mirrored for p in pairs]),
        )
        trained = ctx.ai.learn(
            joined,
            [slot for slot, _, _ in cells],
            photo_ids=[photo.sha256 for _, photo, _ in cells],
            case_id=case.id,
            source="export",
        )
    except Exception as exc:
        return {"error": "failed", "message": _reported("learning_failed", exc)}
    log.info("export_learned", flips=len(flips), samples=len(cells), trained=trained)
    return {"samples": len(cells), "trained": trained}


def _cells(case: CaseRecord) -> list[tuple[Slot, PhotoRecord, EditRecord]]:
    """The placed photos, the ones the composite shows."""
    return [
        (p.slot, p, p.edit)
        for p in case.photos
        if p.slot is not None and p.state == "ready" and p.edit is not None
    ]


# -- zips ----------------------------------------------------------------------------------


def export_zip(
    ctx: AppContext,
    job_id: str,
    *,
    case_id: str | None = None,
    patient_id: str | None = None,
) -> tuple[Path, str]:
    """Pack a finished export into `dirs.exports/<job_id>.zip`; the zip and its download name.

    The download route deletes the zip after sending it. A file deleted since the export is
    left out.
    """
    library = ctx.library()
    today = local_now().date()
    if case_id is not None and patient_id is None:
        result = _finished(ctx, job_id, "export", case_id)
        case = library.read_case(case_id)
        patient = library.patient(case.patient_id).record
        members = _members(library, result, None)
        name = case_export_zip_name(patient.name, case.title, today)
    elif patient_id is not None and case_id is None:
        result = _finished(ctx, job_id, "export_patient", f"patient:{patient_id}")
        patient = library.patient(patient_id).record
        members = []
        for exported in result["cases"]:
            try:
                folder = library.case_dir(exported["case_id"]).name
            except CaseNotFoundError:
                continue  # deleted since the export
            # Named like the case folder, which is unique within the patient (§3.4 step 3).
            members.extend(_members(library, exported, folder))
        name = patient_export_zip_name(patient.name, today)
    else:
        raise ValueError("export_zip takes either a case_id or a patient_id")
    path = ctx.dirs.exports / f"{job_id}.zip"
    _write_zip(path, members)
    log.info("export_zip_written", members=len(members))
    return path, name


def _finished(ctx: AppContext, job_id: str, kind: str, key: str) -> Mapping[str, Any]:
    job = ctx.jobs.get(job_id)
    if job.state != "done" or job.kind != kind or job.key != key or job.result is None:
        raise JobNotFoundError(_NO_EXPORT_MESSAGE)
    return job.result


def _members(
    library: Library, exported: Mapping[str, Any], folder: str | None
) -> list[tuple[Path, str]]:
    case_dir = library.case_dir(exported["case_id"])
    members: list[tuple[Path, str]] = []
    for file in exported["files"]:
        name = PurePosixPath(file).name
        arcname = name if folder is None else f"{folder}/{name}"
        members.append((library.resolve(case_dir / file), arcname))
    return members


def _write_zip(path: Path, members: Sequence[tuple[Path, str]]) -> None:
    # Written beside `path` and replaced into it, so a download never gets half a zip.
    tmp = path.with_name(f".{path.name}.{uuid4().hex[:8]}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Stored, not deflated: JPEGs do not compress, so deflating them only costs time.
        with zipfile.ZipFile(
            tmp, "w", compression=zipfile.ZIP_STORED, strict_timestamps=False
        ) as zf:
            for source, arcname in members:
                try:
                    zf.write(source, arcname)
                except FileNotFoundError:
                    continue  # deleted since the export; nothing of it was written yet
        os.replace(tmp, path)
    except OSError as exc:
        _discard(tmp)
        reason = exc.strerror or type(exc).__name__
        raise StorageError(f"Creating the zip failed: {reason}") from exc
    except BaseException:
        _discard(tmp)
        raise


# -- failures ------------------------------------------------------------------------------


def _failure(photo_id: str | None, message: str) -> dict[str, Any]:
    return {"photo_id": photo_id, "message": message}


def _reported(event: str, exc: Exception, **fields: Any) -> str:
    """Log a failure without names or paths; the message for the user, which may name things."""
    if isinstance(exc, OSError):
        log.error(event, error=os_error_message(exc), **fields)
    elif isinstance(exc, DioError):
        # A naming error quotes the patient name or title, so only the code is logged.
        log.error(event, code=exc.code, **fields)
    else:
        log.exception(event, **fields)
    return failure_message(exc)
