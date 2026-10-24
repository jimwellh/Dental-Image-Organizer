"""The AI model, the classifier, and teaching over the API (spec §3.5, §4.6, §8.2 item 4)."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import closing
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from dental_image_organizer.api.app import create_app
from dental_image_organizer.core.slots import SLOTS
from dental_image_organizer.ml.samples import SampleStore
from dental_image_organizer.ml.training import CLASSIFIER_FILE
from dental_image_organizer.services.context import AppContext
from support.app import ORIGIN, WAIT_SECONDS, make_context, view_set
from support.fake_model import install_fake_model

WaitJob = Callable[[str | None], dict[str, Any]]
Files = list[tuple[str, bytes]]
SEEDS = (70, 170, 270)


@pytest.fixture
def ctx(tmp_path: Path) -> Iterator[AppContext]:
    # Overrides the shared fixture: the fake model is installed, as after a download.
    context = make_context(tmp_path, model_spec=install_fake_model(tmp_path / "cache" / "models"))
    yield context
    context.close()


def _teach(client: TestClient, wait_job: WaitJob, seed: int) -> dict[str, Any]:
    files = [("files", (name, data, "image/jpeg")) for name, data in view_set(seed)]
    started = client.post("/api/classifier/teach", files=files)
    assert started.status_code == 201, started.text
    wait_job(started.json()["job_id"])
    session: dict[str, Any] = client.get(
        f"/api/classifier/teach/{started.json()['session_id']}"
    ).json()
    assert session["state"] == "ready"
    return session


def _confirm(client: TestClient, session: dict[str, Any]) -> dict[str, Any]:
    response = client.post(
        f"/api/classifier/teach/{session['id']}/confirm", json={"slots": session["proposal"]}
    )
    assert response.status_code == 200, response.text
    view: dict[str, Any] = response.json()
    return view


def test_model_status_and_download(tmp_path: Path) -> None:
    source = install_fake_model(tmp_path / "source")
    payload = (tmp_path / "source" / source.filename).read_bytes()
    seen: list[str] = []

    def serve(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host)
        return httpx.Response(200, content=payload)

    # The default spec first: no model installed, and nothing downloads on a status read.
    with closing(make_context(tmp_path / "default")) as plain, _client(plain) as client:
        status = client.get("/api/model/status").json()
        assert status["available"] is False
        assert status["host"] == "github.com"
        assert status["download_job_id"] is None

    context = make_context(
        tmp_path / "fake",
        model_spec=source,
        http_client=lambda: httpx.Client(transport=httpx.MockTransport(serve)),
    )
    with closing(context), _client(context) as client:
        assert client.get("/api/model/status").json()["available"] is False
        assert seen == []
        started = client.post("/api/model/download")
        assert started.status_code == 200, started.text
        job_id = started.json()["job_id"]
        assert job_id is not None
        done = _wait(client, job_id)
        assert done["result"] == {"available": True}
        assert done["done"] == done["total"] == source.size_bytes
        status = client.get("/api/model/status").json()
        assert status["available"] is True
        assert status["size_bytes"] == source.size_bytes
        assert client.post("/api/model/download").json() == {"job_id": None}
        assert len(seen) == 1


def test_teaching_flow(client: TestClient, ctx: AppContext, wait_job: WaitJob) -> None:
    for seed in SEEDS:
        _confirm(client, _teach(client, wait_job, seed))
    status = client.get("/api/classifier/status").json()
    assert status["trained"] is True
    assert status["min_samples_per_slot"] == 3
    assert set(status["counts"]) == set(SLOTS)
    assert set(status["counts"].values()) == {3}
    assert set(SampleStore(ctx.dirs.classifier).load().sources) == {"teach"}
    assert not any(ctx.dirs.teach.iterdir())

    fourth = _teach(client, wait_job, 370)
    photo = fourth["photos"][0]["id"]
    image = client.get(f"/api/classifier/teach/{fourth['id']}/photos/{photo}/image?size=thumb")
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/jpeg"
    assert (ctx.dirs.teach / fourth["id"]).is_dir()
    assert client.delete(f"/api/classifier/teach/{fourth['id']}").status_code == 204
    assert not (ctx.dirs.teach / fourth["id"]).exists()
    assert client.get(f"/api/classifier/teach/{fourth['id']}").status_code == 404
    assert client.get("/api/patients").json()["patients"] == []


def test_teaching_without_the_model_is_refused(tmp_path: Path) -> None:
    with closing(make_context(tmp_path)) as context, _client(context) as client:
        files = [("files", (name, data, "image/jpeg")) for name, data in view_set()]
        response = client.post("/api/classifier/teach", files=files)
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "model_unavailable"


def test_classifier_status_shows_the_flip_defaults(tmp_path: Path) -> None:
    context = make_context(tmp_path, flip_pins={"lower_left": "none"})
    with closing(context), _client(context) as client:
        flips = client.get("/api/classifier/status").json()["flips"]
    assert flips["lower_left"]["effective"] == "none"
    assert flips["lower_left"]["pinned"] is True
    assert flips["upper_right"]["effective"] == "horizontal"
    assert flips["upper_right"]["pinned"] is False


def test_retrain_rebuilds_the_classifier(
    client: TestClient, ctx: AppContext, wait_job: WaitJob
) -> None:
    for seed in SEEDS:
        _confirm(client, _teach(client, wait_job, seed))
    model_json = ctx.dirs.classifier / CLASSIFIER_FILE
    model_json.unlink()
    assert client.get("/api/classifier/status").json()["trained"] is False
    started = client.post("/api/classifier/retrain")
    assert started.status_code == 200, started.text
    done = wait_job(started.json()["job_id"])
    assert done["result"] == {"trained": True}
    assert model_json.exists()
    assert client.get("/api/classifier/status").json()["trained"] is True


def _client(context: AppContext) -> TestClient:
    app = create_app(context, port=8765, shutdown=lambda: None, web_dir=None)
    return TestClient(app, base_url="http://127.0.0.1:8765", headers={"Origin": ORIGIN})


def _wait(client: TestClient, job_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + WAIT_SECONDS
    while True:
        view: dict[str, Any] = client.get(f"/api/jobs/{job_id}").json()
        if view["state"] in ("done", "failed"):
            assert view["state"] == "done", view
            return view
        assert time.monotonic() < deadline
        time.sleep(0.05)
