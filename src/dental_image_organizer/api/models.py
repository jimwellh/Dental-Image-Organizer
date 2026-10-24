"""Request and response bodies of the HTTP API (spec §4.12, §4.13)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

from dental_image_organizer.services.jobs import JobSnapshot, JobState


class ErrorBody(BaseModel):
    code: str
    message: str
    details: Any | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class Health(BaseModel):
    status: Literal["ok"]
    version: str


class RevealRequest(BaseModel):
    path: str


class JobRef(BaseModel):
    job_id: str | None


class JobView(BaseModel):
    id: str
    kind: str
    state: JobState
    done: int
    total: int
    message: str | None
    result: dict[str, Any] | None
    error: ErrorBody | None

    @classmethod
    def of(cls, snapshot: JobSnapshot) -> JobView:
        failure = snapshot.error
        return cls(
            id=snapshot.id,
            kind=snapshot.kind,
            state=snapshot.state,
            done=snapshot.done,
            total=snapshot.total,
            message=snapshot.message,
            result=dict(snapshot.result) if snapshot.result is not None else None,
            error=ErrorBody(code=failure.code, message=failure.message) if failure else None,
        )
