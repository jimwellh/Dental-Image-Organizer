"""Read and replace the settings (spec §4.11, §4.12)."""

from __future__ import annotations

from fastapi import APIRouter

from dental_image_organizer.api.deps import Ctx
from dental_image_organizer.config import Settings

router = APIRouter()


@router.get("/settings")
async def read_settings(ctx: Ctx) -> Settings:
    return ctx.settings.get()


@router.put("/settings")
def write_settings(body: Settings, ctx: Ctx) -> Settings:
    # A plain def: saving syncs the file to disk and evicting walks the cache folder, so this
    # runs in the thread pool, off the event loop.
    return ctx.apply_settings(body)
