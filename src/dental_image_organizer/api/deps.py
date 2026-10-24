"""Route dependencies: the app context, and CPU-heavy work on its executor."""

from __future__ import annotations

import asyncio
import contextvars
import functools
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import Depends, Request

from dental_image_organizer.services.context import AppContext


def context(request: Request) -> AppContext:
    ctx: AppContext = request.app.state.ctx
    return ctx


Ctx = Annotated[AppContext, Depends(context)]


async def run_cpu[T](ctx: AppContext, fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    # The executor holds WORKERS threads, shared with the jobs; Starlette's own thread pool is
    # not bounded by it. The copied context carries the bound case_id and photo_id along.
    call = functools.partial(contextvars.copy_context().run, fn, *args, **kwargs)
    return await asyncio.get_running_loop().run_in_executor(ctx.executor, call)
