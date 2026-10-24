"""Job status and its Server-Sent Events progress stream (spec §4.12)."""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from dental_image_organizer.api.deps import Ctx
from dental_image_organizer.api.models import JobView
from dental_image_organizer.services.context import AppContext

router = APIRouter()


@router.get("/jobs/{job_id}")
async def read_job(job_id: str, ctx: Ctx) -> JobView:
    return JobView.of(ctx.jobs.get(job_id))


@router.get("/jobs/{job_id}/events")
async def job_events(job_id: str, ctx: Ctx) -> StreamingResponse:
    # Raises here, while an error response can still be sent; inside the stream it could not.
    ctx.jobs.get(job_id)
    return StreamingResponse(
        _events(ctx, job_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


async def _events(ctx: AppContext, job_id: str) -> AsyncIterator[str]:
    async for snapshot in ctx.jobs.watch(job_id):
        data = JobView.of(snapshot).model_dump_json()
        yield f"id: {snapshot.version}\nevent: {snapshot.state}\ndata: {data}\n\n"
