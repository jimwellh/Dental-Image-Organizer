"""One error shape for every response (spec §4.13); domain errors map to a status by class."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from dental_image_organizer.api.models import ErrorBody, ErrorResponse
from dental_image_organizer.errors import (
    ConflictError,
    CorruptImageError,
    DioError,
    InvalidArchiveError,
    InvalidNameError,
    InvalidRequestError,
    ModelIntegrityError,
    ModelUnavailableError,
    NotFoundError,
    OutputTooLargeError,
    PathOutsideLibraryError,
    StorageError,
    UnsupportedFormatError,
)

log = structlog.get_logger(__name__)

INTERNAL_MESSAGE = "An unexpected error occurred; see the log file."
INVALID_MESSAGE = "The request is not valid."

# For the OpenAPI schema of every route: the handlers below answer each error with this body,
# a failed request validation included, so FastAPI's HTTPValidationError is never sent.
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    422: {"model": ErrorResponse, "description": "The request is not valid."},
    "4XX": {"model": ErrorResponse},
    "5XX": {"model": ErrorResponse},
}

STATUS_BY_ERROR: Mapping[type[DioError], int] = MappingProxyType(
    {
        NotFoundError: 404,
        ConflictError: 409,
        InvalidRequestError: 422,
        InvalidArchiveError: 422,
        InvalidNameError: 422,
        OutputTooLargeError: 422,
        CorruptImageError: 422,
        UnsupportedFormatError: 415,
        PathOutsideLibraryError: 400,
        ModelUnavailableError: 503,
        ModelIntegrityError: 502,
        # Its message says how to recover, so it is shown like any other.
        StorageError: 500,
    }
)
_HTTP_CODES: Mapping[int, str] = MappingProxyType({404: "not_found", 405: "method_not_allowed"})


def status_for(exc: DioError) -> int:
    # Along the MRO, so a subclass such as JobNotFoundError takes its family's status.
    for cls in type(exc).__mro__:
        status = STATUS_BY_ERROR.get(cls)
        if status is not None:
            return status
    return 400


def error_response(
    status: int,
    code: str,
    message: str,
    details: Any | None = None,
    *,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorBody(code=code, message=message, details=jsonable_encoder(details))
    )
    return JSONResponse(body.model_dump(), status_code=status, headers=headers)


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DioError, _domain_error)
    app.add_exception_handler(RequestValidationError, _invalid_request)
    app.add_exception_handler(HTTPException, _http_error)
    app.add_exception_handler(Exception, _unexpected)


async def _domain_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, DioError)
    # The message and details may name the patient, and a chained OSError carries a path:
    # they go to the user only.
    log.warning("request_failed", code=exc.code)
    return error_response(status_for(exc), exc.code, str(exc), exc.details)


async def _invalid_request(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    # Not logged: the errors echo the submitted values.
    return error_response(422, "invalid_request", INVALID_MESSAGE, exc.errors())


async def _http_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, HTTPException)
    code = _HTTP_CODES.get(exc.status_code, f"http_{exc.status_code}")
    return error_response(exc.status_code, code, str(exc.detail), headers=exc.headers)


async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
    if isinstance(exc, OSError):
        # The message of an OSError carries its path, which names the patient.
        log.error("request_failed", error=exc.strerror or type(exc).__name__)
    else:
        log.exception("request_failed", exc_info=exc)
    return error_response(500, "internal_error", INTERNAL_MESSAGE)
