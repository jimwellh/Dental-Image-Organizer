"""`GET / POST /api/system/update` and the daily version check (spec §4.12, §7.3, §8.2 item 6)."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from dental_image_organizer import __version__
from dental_image_organizer.api.app import create_app
from dental_image_organizer.config import LauncherPaths, write_launcher
from dental_image_organizer.services.context import AppContext
from dental_image_organizer.services.jobs import JobProgress
from dental_image_organizer.services.updates import UpdateService
from support.app import ORIGIN, WAIT_SECONDS, make_context

POLL_SECONDS = 0.05


class Spawned:
    def __init__(self) -> None:
        self.calls: list[tuple[Path, Path]] = []

    def __call__(self, script: Path, log_file: Path) -> None:
        self.calls.append((script, log_file))


def _on_a_mac(ctx: AppContext, tmp_path: Path) -> Spawned:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("uv", "dio"):
        (bin_dir / name).write_text("")
    write_launcher(
        ctx.dirs.launcher_file, LauncherPaths(uv_path=bin_dir / "uv", dio_path=bin_dir / "dio")
    )
    spawned = Spawned()
    ctx.updates = UpdateService(ctx.dirs, platform="darwin", spawn=spawned)
    return spawned


def _error(response: httpx.Response) -> dict[str, Any]:
    body: dict[str, Any] = response.json()["error"]
    return body


def test_status_without_the_launcher_names_the_terminal_command(client: TestClient) -> None:
    response = client.get("/api/system/update")

    assert response.status_code == 200
    body = response.json()
    assert body["current"] == __version__
    assert body["available"] is False
    assert body["latest"] is None
    assert body["command"] == "uv tool upgrade dental-image-organizer"
    # Off a Mac for the platform, on one for the missing launcher record.
    assert body["can_update"] is False
    assert body["unavailable_reason"]
    assert body["last_failure"] is None


def test_update_is_refused_while_a_job_runs(
    client: TestClient, ctx: AppContext, tmp_path: Path
) -> None:
    spawned = _on_a_mac(ctx, tmp_path)
    release = threading.Event()

    def hold(progress: JobProgress) -> None:
        release.wait(WAIT_SECONDS)

    job = ctx.jobs.submit("hold", hold)
    try:
        response = client.post("/api/system/update")
    finally:
        release.set()
    ctx.jobs.wait(job.id, WAIT_SECONDS)

    assert response.status_code == 409
    assert _error(response)["code"] == "job_running"
    assert spawned.calls == []
    assert client.app.state.shutdown_calls == []  # type: ignore[attr-defined]


def test_update_starts_the_script_then_stops_the_server(
    client: TestClient, ctx: AppContext, tmp_path: Path
) -> None:
    spawned = _on_a_mac(ctx, tmp_path)
    assert client.get("/api/system/update").json()["can_update"] is True

    response = client.post("/api/system/update")

    assert response.status_code == 202
    assert response.json() == {"status": "updating"}
    assert spawned.calls == [(ctx.dirs.update_script, ctx.dirs.update_log_file)]
    assert client.app.state.shutdown_calls == [None]  # type: ignore[attr-defined]


def test_update_where_it_cannot_run_is_a_conflict(
    client: TestClient, ctx: AppContext, tmp_path: Path
) -> None:
    ctx.updates = UpdateService(ctx.dirs, platform="darwin", spawn=Spawned())

    response = client.post("/api/system/update")

    assert response.status_code == 409
    assert _error(response)["code"] == "update_unavailable"
    assert "uv tool upgrade dental-image-organizer" in _error(response)["message"]


def test_a_failed_last_update_is_reported_with_the_log(client: TestClient, ctx: AppContext) -> None:
    ctx.dirs.update_result_file.parent.mkdir(parents=True, exist_ok=True)
    ctx.dirs.update_result_file.write_text('{"status": "failed"}')
    ctx.updates = UpdateService(ctx.dirs)

    failure = client.get("/api/system/update").json()["last_failure"]

    assert failure["log_file"] == str(ctx.dirs.update_log_file)
    assert "failed" in failure["message"]


@pytest.fixture
def checking(tmp_path: Path) -> Iterator[tuple[AppContext, list[httpx.Request]]]:
    """A context whose version check is on and answered by a fake PyPI."""
    requests: list[httpx.Request] = []

    def pypi(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"releases": {"999.0.0": [{"yanked": False}]}})

    context = make_context(
        tmp_path,
        update_check=True,
        http_client=lambda: httpx.Client(transport=httpx.MockTransport(pypi)),
    )
    yield context, requests
    context.close()


def test_the_server_checks_for_a_newer_release_when_it_starts(
    checking: tuple[AppContext, list[httpx.Request]],
) -> None:
    ctx, requests = checking
    app = create_app(ctx, port=8765, shutdown=lambda: None, web_dir=None)

    with TestClient(app, base_url="http://127.0.0.1:8765", headers={"Origin": ORIGIN}) as client:
        deadline = time.monotonic() + WAIT_SECONDS
        while (status := client.get("/api/system/update").json())["latest"] is None:
            assert time.monotonic() < deadline, "the version check never ran"
            time.sleep(POLL_SECONDS)

        assert status["latest"] == "999.0.0"
        assert status["available"] is True
        assert status["checked_at"] is not None
        settings = client.get("/api/settings").json()
        client.put("/api/settings", json={**settings, "update_check": False})
        off = client.get("/api/system/update").json()

    assert len(requests) == 1
    assert str(requests[0].url) == "https://pypi.org/pypi/dental-image-organizer/json"
    assert (off["latest"], off["available"]) == (None, False)
