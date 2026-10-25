"""Route dependencies: the app context, CPU-heavy work on its executor, uploads, and downloads."""

from __future__ import annotations

import asyncio
import contextvars
import functools
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, Request, UploadFile
from fastapi.responses import FileResponse, Response
from starlette.background import BackgroundTask

from dental_image_organizer.errors import InvalidRequestError
from dental_image_organizer.services.context import AppContext
from dental_image_organizer.services.intake import Upload, display_name

# For the OpenAPI schema: these routes answer bytes, not JSON.
JPEG_RESPONSE: dict[int | str, dict[str, Any]] = {
    200: {"content": {"image/jpeg": {}}, "description": "A JPEG image."}
}
ZIP_RESPONSE: dict[int | str, dict[str, Any]] = {
    200: {"content": {"application/zip": {}}, "description": "A zip download."}
}
EVENT_STREAM_RESPONSE: dict[int | str, dict[str, Any]] = {
    200: {
        "content": {"text/event-stream": {"schema": {"type": "string"}}},
        "description": "Server-Sent Events: each event is the job's state, its data a JobView.",
    }
}


def context(request: Request) -> AppContext:
    ctx: AppContext = request.app.state.ctx
    return ctx


Ctx = Annotated[AppContext, Depends(context)]


async def run_cpu[T](ctx: AppContext, fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    # The executor holds WORKERS threads, shared with the jobs, which leave one of them free;
    # Starlette's own thread pool is not bounded by it. The copied context carries the bound
    # case_id and photo_id along.
    call = functools.partial(contextvars.copy_context().run, fn, *args, **kwargs)
    return await asyncio.get_running_loop().run_in_executor(ctx.executor, call)


def uploads(files: Sequence[UploadFile], last_modified: Sequence[int] | None) -> list[Upload]:
    """The service's uploads, streaming from the spooled files the request was parsed into."""
    if last_modified is not None and len(last_modified) != len(files):
        raise InvalidRequestError("Send one last_modified value per file, or none.")
    times: list[datetime | None] = (
        [_local_time(ms) for ms in last_modified] if last_modified else [None] * len(files)
    )
    # display_name first: the raw name may hold a path, and nothing else may see it.
    return [
        Upload(display_name(file.filename or ""), file.file, when)
        for file, when in zip(files, times, strict=True)
    ]


def _local_time(ms: int) -> datetime:
    try:
        # Naive local wall-clock time, as EXIF capture times are (spec §4.4 step 5).
        return datetime.fromtimestamp(ms / 1000)
    except (OverflowError, OSError, ValueError) as exc:
        raise InvalidRequestError("A last_modified value is not a valid time.") from exc


def jpeg(content: bytes) -> Response:
    # Revalidated on every view: the same URL shows a new render after each edit.
    return Response(content, media_type="image/jpeg", headers={"Cache-Control": "no-cache"})


def zip_download(path: Path, name: str) -> FileResponse:
    # Starlette writes filename*= for a name that is not plain ASCII, as patient names often
    # are; the zip is deleted once it is sent (spec §4.9).
    return FileResponse(
        path,
        media_type="application/zip",
        filename=name,
        background=BackgroundTask(path.unlink, missing_ok=True),
    )
