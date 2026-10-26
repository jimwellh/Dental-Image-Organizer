"""Background duties of a running server: idle shutdown (spec §4.3), which stops it after
`idle_shutdown_minutes` without a heartbeat but never while a job runs, and the daily version
check (spec §4.13)."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator, Callable

import structlog
from fastapi import FastAPI

from dental_image_organizer.services.context import AppContext

log = structlog.get_logger(__name__)

IDLE_CHECK_S = 15.0
# How often the server asks whether the day's version check is due; the check itself runs at
# most once a day (`UpdateService.check_due`).
UPDATE_POLL_S = 3600.0


class IdleWatch:
    def __init__(
        self, minutes: Callable[[], int], clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._minutes = minutes
        self._clock = clock
        # The start counts as a beat, so a server whose page never opens still stops.
        self._last = clock()

    def beat(self) -> None:
        self._last = self._clock()

    def expired(self, *, jobs_active: bool) -> bool:
        minutes = self._minutes()
        if minutes == 0 or jobs_active:
            return False
        return self._clock() - self._last >= minutes * 60


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    tasks = [
        asyncio.create_task(_watch_idle(app.state.ctx, app.state.idle, app.state.shutdown)),
        asyncio.create_task(_check_updates(app.state.ctx)),
    ]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task


async def _watch_idle(ctx: AppContext, idle: IdleWatch, shutdown: Callable[[], None]) -> None:
    while True:
        # The module global is read on every round, so tests can shorten it.
        await asyncio.sleep(IDLE_CHECK_S)
        if idle.expired(jobs_active=ctx.jobs.active()):
            log.info("idle_shutdown")
            shutdown()
            return


async def _check_updates(ctx: AppContext) -> None:
    while True:
        # Read on every round, so turning the check off in Settings takes effect at once.
        if ctx.settings.get().update_check and ctx.updates.check_due():
            try:
                # In a thread: the request blocks for up to its timeout.
                await asyncio.to_thread(ctx.updates.check)
            except Exception:
                # `check` handles the expected failures; anything else must not end the loop,
                # or the server would never check again until it restarts.
                log.exception("update_check_crashed")
        await asyncio.sleep(UPDATE_POLL_S)
