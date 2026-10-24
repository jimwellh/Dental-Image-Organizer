"""The API client the API tests share: the app over the `ctx` library, with its lifespan running."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from dental_image_organizer.api.app import create_app
from dental_image_organizer.services.context import AppContext
from support.app import ORIGIN, WAIT_SECONDS

PORT = 8765
POLL_SECONDS = 0.05

WaitJob = Callable[[str | None], dict[str, Any]]


@pytest.fixture
def client(ctx: AppContext) -> Iterator[TestClient]:
    calls: list[None] = []
    app = create_app(ctx, port=PORT, shutdown=lambda: calls.append(None), web_dir=None)
    app.state.shutdown_calls = calls
    # `with` runs the lifespan, so the idle watch is live as in the server.
    with TestClient(
        app, base_url="http://127.0.0.1:8765", headers={"Origin": ORIGIN}
    ) as test_client:
        yield test_client


@pytest.fixture
def wait_job(client: TestClient) -> WaitJob:
    """Poll a job through the API, as the page does, until it is done; its view."""

    def wait(job_id: str | None) -> dict[str, Any]:
        assert job_id is not None
        deadline = time.monotonic() + WAIT_SECONDS
        while True:
            response = client.get(f"/api/jobs/{job_id}")
            assert response.status_code == 200, response.text
            view: dict[str, Any] = response.json()
            if view["state"] in ("done", "failed"):
                assert view["state"] == "done", view
                return view
            assert time.monotonic() < deadline, f"job {job_id} did not finish in time"
            time.sleep(POLL_SECONDS)

    return wait
