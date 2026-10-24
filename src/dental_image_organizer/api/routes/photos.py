"""Uploads, the reference, photo changes, deletion, and photo images (spec §3.1-§3.3, §4.12).

Uploads copy and hash every file, and edits decode and fit whole photos, so each of these runs
on the executor.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import Response

from dental_image_organizer.api.deps import JPEG_RESPONSE, Ctx, jpeg, run_cpu, uploads
from dental_image_organizer.api.models import CaseView, PhotoPatch, UploadResult
from dental_image_organizer.core.slots import Slot
from dental_image_organizer.errors import InvalidEditError
from dental_image_organizer.services import cases, composite_case, procedure_case, renders
from dental_image_organizer.services.cases import CaseDetails
from dental_image_organizer.services.context import AppContext
from dental_image_organizer.services.renders import ImageSize, ImageVariant
from dental_image_organizer.storage.schema import CaseRecord

router = APIRouter()


@router.post("/cases/{case_id}/photos")
async def upload_photos(
    case_id: str,
    ctx: Ctx,
    files: Annotated[list[UploadFile], File()],
    last_modified: Annotated[list[int] | None, Form()] = None,
) -> UploadResult:
    batch = uploads(files, last_modified)
    return UploadResult.of(await run_cpu(ctx, cases.upload_photos, ctx, case_id, batch))


@router.put("/cases/{case_id}/reference")
async def set_reference(
    case_id: str,
    ctx: Ctx,
    file: Annotated[UploadFile, File()],
    last_modified: Annotated[int | None, Form()] = None,
) -> UploadResult:
    [upload] = uploads([file], None if last_modified is None else [last_modified])
    return UploadResult.of(await run_cpu(ctx, procedure_case.set_reference, ctx, case_id, upload))


@router.patch("/cases/{case_id}/photos/{photo_id}")
async def patch_photo(case_id: str, photo_id: str, body: PhotoPatch, ctx: Ctx) -> CaseView:
    return CaseView.of(await run_cpu(ctx, _change, ctx, case_id, photo_id, body))


@router.delete("/cases/{case_id}/photos/{photo_id}")
async def delete_photo(case_id: str, photo_id: str, ctx: Ctx) -> CaseView:
    # A composite case replans its cells without the photo.
    return CaseView.of(await run_cpu(ctx, _delete, ctx, case_id, photo_id))


@router.get(
    "/cases/{case_id}/photos/{photo_id}/image",
    response_class=Response,
    responses=JPEG_RESPONSE,
)
async def photo_image(
    case_id: str,
    photo_id: str,
    ctx: Ctx,
    variant: ImageVariant = "processed",
    size: ImageSize = "preview",
) -> Response:
    content = await run_cpu(
        ctx, renders.photo_image, ctx, case_id, photo_id, variant=variant, size=size
    )
    return jpeg(content)


def _change(ctx: AppContext, case_id: str, photo_id: str, patch: PhotoPatch) -> CaseDetails:
    return cases.details_of(ctx, _apply(ctx, case_id, photo_id, patch))


def _apply(ctx: AppContext, case_id: str, photo_id: str, patch: PhotoPatch) -> CaseRecord:
    """Hand the one change to the service of its kind; a change the case type has no use for
    is refused there."""
    if patch.stage is not None:
        return procedure_case.set_stage(ctx, case_id, photo_id, patch.stage)
    if patch.slot is not None:
        slot: Slot | None = None if patch.slot == "tray" else patch.slot
        return composite_case.move_photo(ctx, case_id, photo_id, slot)
    if patch.flip is not None:
        return composite_case.set_flip(ctx, case_id, photo_id, patch.flip)
    composite = ctx.library().read_case(case_id).type == "composite"
    edit = patch.edit
    if edit is not None:
        if not composite:
            return procedure_case.apply_manual_edit(
                ctx, case_id, photo_id, edit.transform, edit.model
            )
        if edit.model != "similarity":
            # Without a reference there is no perspective to correct (spec §4.7.2 step 4).
            raise InvalidEditError("A composite cell takes a similarity transform only.")
        return composite_case.apply_manual_edit(ctx, case_id, photo_id, edit.transform)
    # PhotoPatch admits exactly one change, so what is left is the reset.
    if composite:
        return composite_case.reset_edit(ctx, case_id, photo_id)
    return procedure_case.reset_edit(ctx, case_id, photo_id)


def _delete(ctx: AppContext, case_id: str, photo_id: str) -> CaseDetails:
    return cases.details_of(ctx, cases.delete_photo(ctx, case_id, photo_id))
