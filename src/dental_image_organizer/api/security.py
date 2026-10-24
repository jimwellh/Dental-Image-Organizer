"""Local-only request checks (spec §4.13): state changes come from the app's own page, and
uploads declare a bounded length."""

from __future__ import annotations

from starlette.datastructures import Headers
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

from dental_image_organizer.api.errors import error_response

MAX_REQUEST_BYTES = 2 * 1024**3
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class LocalRequestGuard:
    """Pure ASGI, so a refused upload is answered before any of its body is read."""

    def __init__(self, app: ASGIApp, *, port: int) -> None:
        self.app = app
        # Together with the trusted hosts, this keeps other sites and DNS rebinding out.
        self._origins = frozenset({f"http://127.0.0.1:{port}", f"http://localhost:{port}"})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            refusal = self._refusal(scope)
            if refusal is not None:
                await refusal(scope, receive, send)
                return
        await self.app(scope, receive, send)

    def _refusal(self, scope: Scope) -> Response | None:
        headers = Headers(scope=scope)
        if scope["method"] in UNSAFE_METHODS and headers.get("origin") not in self._origins:
            return error_response(
                403, "forbidden_origin", "Requests must come from the app's own page."
            )
        content_type = headers.get("content-type", "").partition(";")[0].strip().lower()
        if content_type != "multipart/form-data":
            return None
        # The server reads no more than the declared length, so the declaration bounds the body;
        # a chunked upload has none and could run on without limit.
        length = headers.get("content-length", "")
        if not (length.isascii() and length.isdigit()):
            return error_response(411, "length_required", "Uploads need a Content-Length.")
        if int(length) > MAX_REQUEST_BYTES:
            return error_response(
                413, "request_too_large", "Uploads are limited to 2 GiB per request."
            )
        return None
