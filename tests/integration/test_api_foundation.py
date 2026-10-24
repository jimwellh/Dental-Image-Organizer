"""The API foundation: local-only requests, one error shape, system, settings, jobs, and the UI
mount (spec §4.3, §4.12, §4.13, §8.2 item 7)."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
import structlog
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response
from structlog.contextvars import bound_contextvars
from structlog.testing import capture_logs

from dental_image_organizer import __version__
from dental_image_organizer.api import lifecycle, security
from dental_image_organizer.api.app import create_app
from dental_image_organizer.api.deps import run_cpu
from dental_image_organizer.api.models import JobView
from dental_image_organizer.services import reveal
from dental_image_organizer.services.context import AppContext
from dental_image_organizer.services.jobs import JobProgress
from support.app import ORIGIN, WAIT_SECONDS

PORT = 8765
STEPS = 3


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _app(ctx: AppContext, calls: list[None], **kwargs: Any) -> FastAPI:
    return create_app(ctx, port=PORT, shutdown=lambda: calls.append(None), **kwargs)


def _serve(app: FastAPI) -> TestClient:
    return TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN})


def _without_origin(client: TestClient, method: str, url: str) -> Response:
    request = client.build_request(method, url)
    del request.headers["Origin"]
    return client.send(request)


def _error(response: Response) -> dict[str, Any]:
    body: dict[str, Any] = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "details"}
    error: dict[str, Any] = body["error"]
    return error


def _eventually(condition: Callable[[], bool], within: float) -> bool:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return condition()


def _held(release: threading.Event) -> Callable[[JobProgress], None]:
    def job(progress: JobProgress) -> None:
        release.wait(WAIT_SECONDS)

    return job


def _sse(lines: Iterator[str]) -> Iterator[dict[str, str]]:
    event: dict[str, str] = {}
    for line in lines:
        if line:
            field, _, value = line.partition(": ")
            event[field] = value
        elif event:
            yield event
            event = {}


def test_health_reports_the_version(client: TestClient) -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


def test_a_foreign_host_is_rejected(client: TestClient) -> None:
    response = client.get("/api/health", headers={"Host": "evil.test"})

    assert response.status_code == 400


def test_state_changes_need_the_apps_origin(client: TestClient) -> None:
    refused = [_without_origin(client, "POST", "/api/heartbeat")]
    for origin in ("http://localhost:5173", "http://127.0.0.1:9999"):
        refused.append(client.post("/api/heartbeat", headers={"Origin": origin}))

    for response in refused:
        assert response.status_code == 403
        assert _error(response) == {
            "code": "forbidden_origin",
            "message": "Requests must come from the app's own page.",
            "details": None,
        }
    assert (
        client.post("/api/heartbeat", headers={"Origin": "http://localhost:8765"}).status_code
        == 204
    )
    assert _without_origin(client, "GET", "/api/health").status_code == 200


def test_paths_outside_the_library_are_rejected(
    client: TestClient, ctx: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    revealed: list[Path] = []
    monkeypatch.setattr(reveal, "reveal", revealed.append)
    library = ctx.library()
    patient = library.create_patient("Alex Wang", None)
    folder = library.case_dir(library.create_case(patient.id, "procedure", "implant 16").id)

    outside = client.post("/api/system/reveal", json={"path": "../outside"})
    missing = client.post("/api/system/reveal", json={"path": "nobody"})
    shown = client.post("/api/system/reveal", json={"path": str(folder)})

    assert outside.status_code == 400
    assert _error(outside)["code"] == "path_outside_library"
    assert missing.status_code == 404
    assert _error(missing) == {
        "code": "not_found",
        "message": "Nothing to reveal at that path.",
        "details": {},
    }
    assert shown.status_code == 204
    assert revealed == [folder.resolve()]


def test_uploads_need_a_bounded_length(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def chunked() -> Iterator[bytes]:
        yield b"--x\r\n"
        yield b"--x--\r\n"

    unbounded = client.post(
        "/api/heartbeat",
        content=chunked(),
        headers={"Content-Type": "multipart/form-data; boundary=x"},
    )
    bounded = client.post("/api/heartbeat", files={"file": ("a.jpg", b"0123456789")})
    monkeypatch.setattr(security, "MAX_REQUEST_BYTES", 10)
    too_large = client.post("/api/heartbeat", files={"file": ("a.jpg", b"0123456789")})

    assert unbounded.status_code == 411
    assert _error(unbounded)["code"] == "length_required"
    assert _error(unbounded)["message"] == "Uploads need a Content-Length."
    assert bounded.status_code == 204
    assert too_large.status_code == 413
    assert _error(too_large)["code"] == "request_too_large"
    assert _error(too_large)["message"] == "Uploads are limited to 2 GiB per request."


def test_errors_have_one_shape(client: TestClient) -> None:
    settings = client.get("/api/settings").json()
    with capture_logs() as logs:
        unknown_path = client.get("/api/nope")
        unknown_job = client.get("/api/jobs/0123456789abcdef")
    with capture_logs() as validation_logs:
        invalid = client.put("/api/settings", json={**settings, "jpeg_quality": 101})

    assert unknown_path.status_code == 404
    assert _error(unknown_path)["code"] == "not_found"
    assert unknown_job.status_code == 404
    assert _error(unknown_job)["code"] == "job_not_found"
    # A domain error logs its code only: messages and chained OSErrors may name the patient.
    assert {"event": "request_failed", "code": "job_not_found", "log_level": "warning"} in logs
    assert invalid.status_code == 422
    error = _error(invalid)
    assert error["code"] == "invalid_request"
    assert error["message"] == "The request is not valid."
    assert [d["loc"] for d in error["details"]] == [["body", "jpeg_quality"]]
    # The validation errors echo request values, so nothing is logged.
    assert validation_logs == []

    def boom() -> None:
        raise RuntimeError("Alex Wang")

    assert isinstance(client.app, FastAPI)
    client.app.add_api_route("/test-only/boom", boom)
    # The server-error middleware re-raises after answering; only a server would log that.
    quiet = TestClient(client.app, base_url=ORIGIN, raise_server_exceptions=False)
    with capture_logs() as crash_logs:
        crashed = quiet.get("/test-only/boom")

    assert crashed.status_code == 500
    assert _error(crashed) == {
        "code": "internal_error",
        "message": "An unexpected error occurred; see the log file.",
        "details": None,
    }
    assert "Alex Wang" not in crashed.text
    assert [e["event"] for e in crash_logs] == ["request_failed"]
    assert crash_logs[0]["log_level"] == "error"


def test_the_api_docs_page_is_not_served(client: TestClient) -> None:
    schema = client.get("/api/openapi.json")

    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
    assert schema.status_code == 200
    assert "/api/health" in schema.json()["paths"]


def test_heartbeat_and_shutdown(ctx: AppContext) -> None:
    clock = FakeClock()
    calls: list[None] = []
    app = _app(ctx, calls, web_dir=None, clock=clock)

    with _serve(app) as client:
        clock.advance(31 * 60)
        assert app.state.idle.expired(jobs_active=False)
        assert client.post("/api/heartbeat").status_code == 204
        assert not app.state.idle.expired(jobs_active=False)
        stopping = client.post("/api/system/shutdown")

        assert stopping.status_code == 202
        assert stopping.json() == {"status": "stopping"}
        assert len(calls) == 1


def test_settings_round_trip_and_refuse_moving_the_library_during_a_job(
    client: TestClient, ctx: AppContext, tmp_path: Path
) -> None:
    settings = client.get("/api/settings").json()

    saved = client.put("/api/settings", json={**settings, "jpeg_quality": 80})

    assert saved.status_code == 200
    assert saved.json() == {**settings, "jpeg_quality": 80}
    assert client.get("/api/settings").json() == saved.json()
    assert ctx.settings.get().jpeg_quality == 80

    release = threading.Event()
    job = ctx.jobs.submit("hold", _held(release))
    try:
        moved = client.put(
            "/api/settings", json={**saved.json(), "library_root": str(tmp_path / "moved")}
        )
    finally:
        release.set()
    ctx.jobs.wait(job.id, WAIT_SECONDS)

    assert moved.status_code == 409
    assert _error(moved)["code"] == "job_running"
    assert ctx.settings.get().library_root == tmp_path / "library"


def test_job_events_stream_until_done(client: TestClient, ctx: AppContext) -> None:
    # Each step waits for the stream to show the previous one, so no step can be coalesced.
    gates = [threading.Event() for _ in range(STEPS)]

    def steps(progress: JobProgress) -> dict[str, int]:
        for done, gate in enumerate(gates, start=1):
            gate.wait(WAIT_SECONDS)
            progress(done, STEPS, f"step {done}")
        return {"steps": STEPS}

    job = ctx.jobs.submit("demo", steps)
    events: list[dict[str, str]] = []
    try:
        with client.stream("GET", f"/api/jobs/{job.id}/events") as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            assert response.headers["cache-control"] == "no-cache"
            for event in _sse(response.iter_lines()):
                events.append(event)
                view = JobView.model_validate_json(event["data"])
                if view.done < STEPS:
                    gates[view.done].set()
    finally:
        for gate in gates:
            gate.set()

    views = [JobView.model_validate_json(e["data"]) for e in events]
    versions = [int(e["id"]) for e in events]
    assert versions == sorted(set(versions))
    assert [e["event"] for e in events] == [v.state for v in views]
    assert {1, 2} <= {v.done for v in views if v.state == "running"}
    assert [v.done for v in views] == sorted(v.done for v in views)
    assert events[-1]["event"] == "done"
    assert views[-1].done == STEPS
    assert views[-1].result == {"steps": STEPS}
    assert all(v.id == job.id and v.kind == "demo" for v in views)

    unknown = client.get("/api/jobs/0123456789abcdef/events")
    assert unknown.status_code == 404
    assert _error(unknown)["code"] == "job_not_found"


def test_the_spa_falls_back_to_index(ctx: AppContext, tmp_path: Path) -> None:
    web = tmp_path / "web"
    (web / "assets").mkdir(parents=True)
    index = "<!doctype html><div id=root></div>"
    (web / "index.html").write_text(index, encoding="utf-8")
    (web / "assets" / "a.js").write_text("console.log('a')", encoding="utf-8")

    with _serve(_app(ctx, [], web_dir=web)) as client:
        route = client.get("/cases/abc")
        root = client.get("/")
        asset = client.get("/assets/a.js")
        api = client.get("/api/nope")

    for page in (route, root):
        assert page.status_code == 200
        assert page.text == index
        assert page.headers["cache-control"] == "no-cache"
    assert asset.status_code == 200
    assert asset.text == "console.log('a')"
    assert "cache-control" not in asset.headers
    assert api.status_code == 404
    assert _error(api)["code"] == "not_found"


def test_without_a_build_the_root_explains_itself(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "npm run build" in response.text


def test_idle_shutdown_runs_from_the_lifespan(
    ctx: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(lifecycle, "IDLE_CHECK_S", 0.01)
    clock = FakeClock()
    calls: list[None] = []
    release = threading.Event()
    app = _app(ctx, calls, web_dir=None, clock=clock)

    with _serve(app):
        # Changed after the app was built: the watch reads the setting on every check.
        ctx.apply_settings(ctx.settings.get().model_copy(update={"idle_shutdown_minutes": 1}))
        job = ctx.jobs.submit("hold", _held(release))
        try:
            clock.advance(61)
            time.sleep(0.3)
            assert calls == []
        finally:
            release.set()
        ctx.jobs.wait(job.id, WAIT_SECONDS)

        assert _eventually(lambda: len(calls) == 1, within=2.0)
        time.sleep(0.1)
        assert len(calls) == 1


def test_cpu_work_keeps_the_log_context(ctx: AppContext) -> None:
    def work(a: int, *, b: int) -> tuple[str, dict[str, Any], int]:
        return threading.current_thread().name, structlog.contextvars.get_contextvars(), a + b

    async def handler() -> tuple[str, dict[str, Any], int]:
        with bound_contextvars(case_id="c1"):
            return await run_cpu(ctx, work, 1, b=2)

    thread, bound, total = asyncio.run(handler())

    assert thread.startswith("dio-worker")
    assert bound == {"case_id": "c1"}
    assert total == 3
