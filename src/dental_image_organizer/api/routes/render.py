"""Sequence and composite previews (spec §3.2 step 6, §3.3 step 5, §4.12)."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response

from dental_image_organizer.api.deps import JPEG_RESPONSE, Ctx, jpeg, run_cpu
from dental_image_organizer.errors import InvalidRequestError
from dental_image_organizer.services import renders
from dental_image_organizer.services.context import AppContext
from dental_image_organizer.services.renders import RenderSize
from dental_image_organizer.storage.schema import CaseType

router = APIRouter()

_WRONG_TYPE: dict[CaseType, str] = {
    "procedure": "This is a composite case.",
    "composite": "This is a procedure case.",
}


@router.get("/cases/{case_id}/render/sequence", response_class=Response, responses=JPEG_RESPONSE)
async def render_sequence(case_id: str, ctx: Ctx, size: RenderSize = "preview") -> Response:
    return jpeg(await run_cpu(ctx, _sequence, ctx, case_id, size))


@router.get("/cases/{case_id}/render/composite", response_class=Response, responses=JPEG_RESPONSE)
async def render_composite(case_id: str, ctx: Ctx, size: RenderSize = "preview") -> Response:
    return jpeg(await run_cpu(ctx, _composite, ctx, case_id, size))


def _sequence(ctx: AppContext, case_id: str, size: RenderSize) -> bytes:
    _expect(ctx, case_id, "procedure")
    return renders.sequence_image(ctx, case_id, size=size)


def _composite(ctx: AppContext, case_id: str, size: RenderSize) -> bytes:
    _expect(ctx, case_id, "composite")
    return renders.composite_image(ctx, case_id, size=size)


def _expect(ctx: AppContext, case_id: str, kind: CaseType) -> None:
    # The renders take any case: a composite's cells would otherwise come back as a sequence.
    if ctx.library().read_case(case_id).type != kind:
        raise InvalidRequestError(_WRONG_TYPE[kind])
