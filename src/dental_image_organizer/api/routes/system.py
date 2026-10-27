"""Health, heartbeat, quit, reveal, and the in-app update (spec §4.3, §4.12, §7.3)."""

from __future__ import annotations

import os

from fastapi import APIRouter, BackgroundTasks, Request

from dental_image_organizer import __version__
from dental_image_organizer.api.deps import Ctx
from dental_image_organizer.api.models import (
    Health,
    RevealRequest,
    Stopping,
    UpdateStatus,
    Updating,
)
from dental_image_organizer.errors import NotFoundError
from dental_image_organizer.services import reveal

router = APIRouter()


@router.get("/health")
async def health() -> Health:
    return Health(status="ok", version=__version__)


@router.post("/heartbeat", status_code=204)
async def heartbeat(request: Request) -> None:
    request.app.state.idle.beat()


@router.post("/system/shutdown", status_code=202)
async def shutdown(request: Request, background: BackgroundTasks) -> Stopping:
    # After the response, so the page learns that the server is stopping.
    background.add_task(request.app.state.shutdown)
    return Stopping(status="stopping")


@router.post("/system/reveal", status_code=204)
def reveal_path(body: RevealRequest, ctx: Ctx) -> None:
    # A plain def: resolving the path touches the disk, so it runs in the thread pool.
    target = ctx.library().resolve(body.path)
    if not target.exists():
        raise NotFoundError("Nothing to reveal at that path.")
    reveal.reveal(target)


@router.get("/system/update")
def update_status(ctx: Ctx) -> UpdateStatus:
    # A plain def: it reads the launcher record from disk.
    info = ctx.updates.info(check_enabled=ctx.settings.get().update_check)
    return UpdateStatus.of(info)


@router.post("/system/update", status_code=202)
def start_update(request: Request, background: BackgroundTasks, ctx: Ctx) -> Updating:
    # The update script waits for this process to exit before it upgrades (spec §7.3).
    ctx.updates.start(ctx.jobs, server_pid=os.getpid())
    background.add_task(request.app.state.shutdown)
    return Updating(status="updating")
