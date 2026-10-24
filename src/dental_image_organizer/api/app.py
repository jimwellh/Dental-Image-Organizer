"""The local-only FastAPI app (spec §4.12, §4.13): the API under `/api`, then the built UI."""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from starlette.exceptions import HTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import Response
from starlette.staticfiles import PathLike, StaticFiles
from starlette.types import Scope

from dental_image_organizer.api.errors import install_error_handlers
from dental_image_organizer.api.lifecycle import IdleWatch, lifespan
from dental_image_organizer.api.routes import jobs, settings, system
from dental_image_organizer.api.security import LocalRequestGuard
from dental_image_organizer.services.context import AppContext

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

_ALL_METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
_NOT_BUILT = (
    "<!doctype html><title>Dental Image Organizer</title>"
    "<p>Dental Image Organizer is running, but its interface is not built. "
    "Developers: run npm run build in frontend/.</p>"
)


class SpaFiles(StaticFiles):
    """The built UI. A path it does not hold is one of the SPA's own routes."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
            return await super().get_response("index.html", scope)

    def file_response(
        self,
        full_path: PathLike,
        stat_result: os.stat_result,
        scope: Scope,
        status_code: int = 200,
    ) -> Response:
        response = super().file_response(full_path, stat_result, scope, status_code)
        if os.path.basename(full_path) == "index.html":
            # Revalidated on every load, so the asset names of an updated build reach the page.
            response.headers["Cache-Control"] = "no-cache"
        return response


def create_app(
    ctx: AppContext,
    *,
    port: int,
    shutdown: Callable[[], None],
    web_dir: Path | None = WEB_DIR,
    clock: Callable[[], float] = time.monotonic,
) -> FastAPI:
    app = FastAPI(
        # The Swagger and ReDoc pages load their scripts from a CDN (spec §4.13: no outbound
        # calls); the schema stays for the frontend's type generator.
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
        # No telemetry (spec §4.13): FastAPI would otherwise export to an OTLP endpoint named
        # in the environment.
        telemetry={"tracing": False, "metrics": False, "logs": False, "auto_configure": False},
    )
    app.state.ctx = ctx
    app.state.idle = IdleWatch(lambda: ctx.settings.get().idle_shutdown_minutes, clock)
    app.state.shutdown = shutdown
    install_error_handlers(app)
    # Each addition wraps the ones before it, so the host check runs first.
    app.add_middleware(LocalRequestGuard, port=port)
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"], www_redirect=False
    )
    for router in (system.router, settings.router, jobs.router):
        app.include_router(router, prefix="/api")
    # After every router: an unknown API path answers JSON, never the SPA's index.html.
    app.add_api_route(
        "/api/{rest:path}", _unknown_api, methods=_ALL_METHODS, include_in_schema=False
    )
    if web_dir is not None and (web_dir / "index.html").is_file():
        app.mount("/", SpaFiles(directory=web_dir, html=True), name="web")
    else:
        app.add_api_route("/", _not_built, methods=["GET"], include_in_schema=False)
    return app


async def _unknown_api() -> None:
    raise HTTPException(status_code=404)


async def _not_built() -> HTMLResponse:
    return HTMLResponse(_NOT_BUILT)
