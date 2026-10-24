"""Composite slots: drag-to-swap and automatic reassignment (spec §3.3, §4.12)."""

from __future__ import annotations

from fastapi import APIRouter

from dental_image_organizer.api.deps import Ctx, run_cpu
from dental_image_organizer.api.models import CaseView, JobRef, SlotMap
from dental_image_organizer.services import cases, composite_case
from dental_image_organizer.services.cases import CaseDetails
from dental_image_organizer.services.context import AppContext

router = APIRouter()


@router.put("/cases/{case_id}/composite/slots")
async def set_slots(case_id: str, body: SlotMap, ctx: Ctx) -> CaseView:
    # Every placement change replans the cells, which takes about half a second.
    return CaseView.of(await run_cpu(ctx, _set_slots, ctx, case_id, body))


@router.post("/cases/{case_id}/composite/assign")
def assign(case_id: str, ctx: Ctx) -> JobRef:
    # A plain def: it only reads the case and queues the job.
    return JobRef(job_id=composite_case.start_auto_assign(ctx, case_id))


def _set_slots(ctx: AppContext, case_id: str, body: SlotMap) -> CaseDetails:
    return cases.details_of(ctx, composite_case.set_slots(ctx, case_id, body.slots))
