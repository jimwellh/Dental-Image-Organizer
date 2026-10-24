"""Case archive download and import (spec §4.11, §4.12, amendment 3)."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Annotated, BinaryIO

import structlog
from fastapi import APIRouter, File, Response, UploadFile
from fastapi.responses import FileResponse
from structlog.contextvars import bound_contextvars

from dental_image_organizer.api.deps import ZIP_RESPONSE, Ctx, run_cpu, zip_download
from dental_image_organizer.api.models import ImportResult
from dental_image_organizer.core.naming import case_archive_name
from dental_image_organizer.errors import StorageError
from dental_image_organizer.services.context import AppContext
from dental_image_organizer.services.intake import COPY_CHUNK
from dental_image_organizer.storage.archive import (
    ConflictPolicy,
    ImportOutcome,
    import_case_archive,
    write_case_archive,
)
from dental_image_organizer.storage.schema import new_id

log = structlog.get_logger(__name__)

router = APIRouter()


@router.get("/cases/{case_id}/archive", response_class=FileResponse, responses=ZIP_RESPONSE)
async def case_archive(case_id: str, ctx: Ctx) -> FileResponse:
    path, name = await run_cpu(ctx, _write, ctx, case_id)
    return zip_download(path, name)


@router.post(
    "/archive/import",
    status_code=201,
    responses={200: {"model": ImportResult, "description": "Skipped: already in the library."}},
)
async def import_archive(
    ctx: Ctx,
    response: Response,
    file: Annotated[UploadFile, File()],
    on_conflict: ConflictPolicy = "ask",
) -> ImportResult:
    """201 for an imported case; 200 when `skip` met a case already in the library."""
    outcome = await run_cpu(ctx, _import, ctx, file.file, on_conflict)
    if outcome.status == "skipped":
        response.status_code = 200
    return ImportResult.of(outcome)


def _write(ctx: AppContext, case_id: str) -> tuple[Path, str]:
    library = ctx.library()
    out = ctx.dirs.exports / f"{new_id()}.zip"
    with bound_contextvars(case_id=case_id):
        # Held while the zip is written: the archive reads case.json, patient.json, and the
        # originals straight from the folder, and every writer, rename, and deletion of the
        # case takes this lock too.
        with ctx.case_locks.hold(case_id):
            case = library.read_case(case_id)
            patient = library.patient(case.patient_id).record
            name = case_archive_name(patient.name, case.title, case.created_at.date())
            try:
                out.parent.mkdir(parents=True, exist_ok=True)
                write_case_archive(library.case_dir(case_id), out)
            except OSError as exc:
                raise StorageError(f"Writing the archive failed: {_reason(exc)}") from exc
        log.info("case_archive_written", photos=len(case.photos))
        return out, name


def _import(ctx: AppContext, upload: BinaryIO, on_conflict: ConflictPolicy) -> ImportOutcome:
    # The archive reader needs a file of its own; the copy never outlives the request.
    copy = ctx.dirs.exports / f"{new_id()}.import"
    try:
        try:
            copy.parent.mkdir(parents=True, exist_ok=True)
            with copy.open("xb") as out:
                shutil.copyfileobj(upload, out, COPY_CHUNK)
            outcome = import_case_archive(ctx.library(), copy, on_conflict=on_conflict)
        except OSError as exc:
            raise StorageError(f"Importing the archive failed: {_reason(exc)}") from exc
    finally:
        try:
            copy.unlink(missing_ok=True)
        except OSError as exc:
            # The cache folder is emptied at the next start; the import's outcome stands.
            log.warning("import_copy_remove_failed", error=_reason(exc))
    log.info("case_archive_imported", case_id=outcome.case_id, status=outcome.status)
    return outcome


def _reason(exc: OSError) -> str:
    # strerror never carries the path, unlike str(exc).
    return exc.strerror or type(exc).__name__
