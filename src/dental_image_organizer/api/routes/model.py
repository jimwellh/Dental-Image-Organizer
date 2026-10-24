"""The AI model: its status and the consented download (spec §4.6)."""

from __future__ import annotations

from urllib.parse import urlparse

from fastapi import APIRouter

from dental_image_organizer.api.deps import Ctx
from dental_image_organizer.api.models import JobRef, ModelStatus
from dental_image_organizer.services.context import AppContext
from dental_image_organizer.services.jobs import JobProgress

router = APIRouter()

# Downloads share one `<filename>.part` in the cache, so this key keeps them from overlapping.
DOWNLOAD_KEY = "model"


@router.get("/model/status")
def model_status(ctx: Ctx) -> ModelStatus:
    active = ctx.jobs.active_job(DOWNLOAD_KEY)
    return ModelStatus(
        available=ctx.ai.model_path() is not None,
        size_bytes=ctx.ai.spec.size_bytes,
        host=urlparse(ctx.ai.spec.url).hostname or "",
        download_job_id=active.id if active else None,
    )


@router.post("/model/download")
def download_model(ctx: Ctx) -> JobRef:
    # The only route that leads to an outbound call, and only because the user consented.
    if ctx.ai.model_path() is not None:
        return JobRef(job_id=None)
    active = ctx.jobs.active_job(DOWNLOAD_KEY)
    if active is not None:
        return JobRef(job_id=active.id)
    job = ctx.jobs.submit(
        "model_download",
        lambda progress: _download(ctx, progress),
        key=DOWNLOAD_KEY,
        total=ctx.ai.spec.size_bytes,
    )
    return JobRef(job_id=job.id)


def _download(ctx: AppContext, progress: JobProgress) -> dict[str, bool]:
    ctx.ai.download(lambda done, total: progress(done, total))
    return {"available": True}
