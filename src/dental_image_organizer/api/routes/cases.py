"""Create, read, change, and delete cases (spec §4.12)."""

from __future__ import annotations

from fastapi import APIRouter

from dental_image_organizer.api.deps import Ctx, run_cpu
from dental_image_organizer.api.models import CaseCreate, CasePatch, CaseView, CompositePatch
from dental_image_organizer.errors import InvalidRequestError
from dental_image_organizer.services import cases
from dental_image_organizer.services.cases import CaseDetails
from dental_image_organizer.services.context import AppContext
from dental_image_organizer.storage.schema import CaseRecord, CompositeRecord, SequenceRecord

router = APIRouter()


@router.post("/cases", status_code=201)
def create_case(body: CaseCreate, ctx: Ctx) -> CaseView:
    # Plain defs here: each writes or reads a few small JSON files, in the thread pool.
    case = cases.create_case(
        ctx,
        body.type,
        body.title,
        patient_id=body.patient_id,
        patient_name=body.patient_name,
        patient_note=body.patient_note,
    )
    return CaseView.of(cases.details_of(ctx, case))


@router.get("/cases/{case_id}")
def read_case(case_id: str, ctx: Ctx) -> CaseView:
    return CaseView.of(cases.case_details(ctx, case_id))


@router.patch("/cases/{case_id}")
async def patch_case(case_id: str, body: CasePatch, ctx: Ctx) -> CaseView:
    # A layout change replans every cell, and a rename moves the folder.
    return CaseView.of(await run_cpu(ctx, _change, ctx, case_id, body))


@router.delete("/cases/{case_id}", status_code=204)
def delete_case(case_id: str, ctx: Ctx) -> None:
    cases.delete_case(ctx, case_id)


def _change(ctx: AppContext, case_id: str, body: CasePatch) -> CaseDetails:
    layout = None
    if body.composite is not None:
        layout = _layout(ctx.library().read_case(case_id), body.composite)
    sequence = None
    if body.sequence is not None:
        sequence = SequenceRecord(photo_ids=body.sequence.photo_ids, gap_px=body.sequence.gap_px)
    case = cases.change_case(
        ctx,
        case_id,
        title=body.title,
        output_scale=body.output_scale,
        sequence=sequence,
        layout=layout,
    )
    return cases.details_of(ctx, case)


def _layout(case: CaseRecord, patch: CompositePatch) -> CompositeRecord:
    """The case's layout with the fields the request sent."""
    if case.composite is None:
        raise InvalidRequestError("This is a procedure case.")
    sent = {name: getattr(patch, name) for name in patch.model_fields_set}
    return CompositeRecord.model_validate(case.composite.model_dump() | sent)
