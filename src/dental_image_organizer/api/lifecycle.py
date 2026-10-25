"""Idle shutdown (spec §4.3): the server stops after `idle_shutdown_minutes` without a
heartbeat, but never while a job runs."""

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
    watch = asyncio.create_task(_watch_idle(app.state.ctx, app.state.idle, app.state.shutdown))
    try:
        yield
    finally:
        watch.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watch


async def _watch_idle(ctx: AppContext, idle: IdleWatch, shutdown: Callable[[], None]) -> None:
    while True:
        # The module global is read on every round, so tests can shorten it.
        await asyncio.sleep(IDLE_CHECK_S)
        if idle.expired(jobs_active=ctx.jobs.active()):
            log.info("idle_shutdown")
            shutdown()
            return
