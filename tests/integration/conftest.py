"""The API client the API tests share: the app over the `ctx` library, with its lifespan running."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from dental_image_organizer.api.app import create_app
from dental_image_organizer.services.context import AppContext
from support.app import ORIGIN

PORT = 8765


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
