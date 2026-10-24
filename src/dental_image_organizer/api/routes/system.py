"""Health, heartbeat, quit, and reveal (spec §4.3, §4.12)."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Request

from dental_image_organizer import __version__
from dental_image_organizer.api.deps import Ctx
from dental_image_organizer.api.models import Health, RevealRequest
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
async def shutdown(request: Request, background: BackgroundTasks) -> dict[str, str]:
    # After the response, so the page learns that the server is stopping.
    background.add_task(request.app.state.shutdown)
    return {"status": "stopping"}


@router.post("/system/reveal", status_code=204)
def reveal_path(body: RevealRequest, ctx: Ctx) -> None:
    # A plain def: resolving the path touches the disk, so it runs in the thread pool.
    target = ctx.library().resolve(body.path)
    if not target.exists():
        raise NotFoundError("Nothing to reveal at that path.")
    reveal.reveal(target)
