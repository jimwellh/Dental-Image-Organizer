"""Classifier status, retraining, and teaching sessions (spec §3.5, §4.12)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Form, Response, UploadFile

from dental_image_organizer.api.deps import JPEG_RESPONSE, Ctx, jpeg, run_cpu, uploads
from dental_image_organizer.api.models import (
    ClassifierView,
    JobRef,
    SkippedView,
    TeachConfirm,
    TeachSessionView,
    TeachUpload,
)
from dental_image_organizer.ml.classifier import MIN_SAMPLES_PER_SLOT
from dental_image_organizer.services.ai import ClassifierStatus
from dental_image_organizer.services.composite_case import flip_history
from dental_image_organizer.services.context import AppContext
from dental_image_organizer.services.flips import flip_status
from dental_image_organizer.services.teach import TeachSize

router = APIRouter()

RETRAIN_KEY = "classifier"


def _view(ctx: AppContext, status: ClassifierStatus) -> ClassifierView:
    # A damaged history shows the defaults planning falls back to, and why.
    history, damaged = flip_history(ctx)
    flips = flip_status(ctx.settings.get(), history)
    return ClassifierView.of(status, flips, flips_damaged=damaged, min_samples=MIN_SAMPLES_PER_SLOT)


@router.get("/classifier/status")
def classifier_status(ctx: Ctx) -> ClassifierView:
    # A plain `def`: the status reads the sample file, so it runs off the event loop.
    return _view(ctx, ctx.ai.status())


@router.post("/classifier/retrain")
def retrain(ctx: Ctx) -> JobRef:
    job = ctx.jobs.submit(
        "retrain", lambda progress: {"trained": ctx.ai.retrain()}, key=RETRAIN_KEY
    )
    return JobRef(job_id=job.id)


@router.post("/classifier/teach", status_code=201)
async def start_teaching(
    ctx: Ctx,
    files: Annotated[list[UploadFile], File()],
    last_modified: Annotated[list[int] | None, Form()] = None,
) -> TeachUpload:
    session, skipped = await run_cpu(ctx, ctx.teach.start, uploads(files, last_modified))
    return TeachUpload(
        session_id=session.id if session else None,
        job_id=session.job_id if session else None,
        skipped=[SkippedView(name=s.name, reason=s.reason, message=s.message) for s in skipped],
    )


@router.get("/classifier/teach/{session_id}")
def read_session(session_id: str, ctx: Ctx) -> TeachSessionView:
    return TeachSessionView.of(ctx.teach.get(session_id))


@router.get(
    "/classifier/teach/{session_id}/photos/{photo_id}/image",
    response_class=Response,
    responses=JPEG_RESPONSE,
)
async def teach_image(
    session_id: str, photo_id: str, ctx: Ctx, size: TeachSize = "thumb"
) -> Response:
    return jpeg(await run_cpu(ctx, ctx.teach.image, session_id, photo_id, size))


@router.post("/classifier/teach/{session_id}/confirm")
async def confirm_session(session_id: str, body: TeachConfirm, ctx: Ctx) -> ClassifierView:
    status = await run_cpu(ctx, ctx.teach.confirm, session_id, body.slots, body.flips)
    return await run_cpu(ctx, _view, ctx, status)


@router.delete("/classifier/teach/{session_id}", status_code=204)
def cancel_session(session_id: str, ctx: Ctx) -> None:
    ctx.teach.cancel(session_id)
