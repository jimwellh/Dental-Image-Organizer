"""A composite case over the API: slots, flips, the preview, export, and reassignment (spec
§3.3, §8.2 item 2)."""

from __future__ import annotations

import io
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from structlog.testing import capture_logs

from dental_image_organizer.core.slots import DEFAULT_FLIPS, SLOTS
from dental_image_organizer.ml.samples import SampleStore
from dental_image_organizer.services.context import AppContext
from support.app import make_context, view_set
from support.fake_model import install_fake_model

WaitJob = Callable[[str | None], dict[str, Any]]
Files = list[tuple[str, bytes]]

ROW_MAJOR = {f"IMG_{i + 1:04d}.jpg": slot for i, slot in enumerate(SLOTS)}
COMPOSITE = "Alex Wang_full mouth_Composite_20261016.jpg"
FLIPS_DAMAGED = (
    "The flip history is damaged; move flips.json out of the classifier folder to start over."
)


@pytest.fixture
def ctx(tmp_path: Path) -> Iterator[AppContext]:
    # Overrides the shared fixture, so the `client` of this module serves a library with the
    # AI model installed; the classifier is untrained, so slots follow the shooting order.
    context = make_context(tmp_path, model_spec=install_fake_model(tmp_path / "cache" / "models"))
    yield context
    context.close()


@pytest.fixture(scope="module")
def nine() -> Files:
    return view_set()


def _composite(client: TestClient, nine: Files, wait_job: WaitJob) -> str:
    body = {"type": "composite", "title": "full mouth", "patient_name": "Alex Wang"}
    created = client.post("/api/cases", json=body)
    assert created.status_code == 201, created.text
    case_id: str = created.json()["case"]["id"]
    files = [("files", (name, data, "image/jpeg")) for name, data in nine]
    added = client.post(f"/api/cases/{case_id}/photos", files=files)
    assert added.status_code == 200, added.text
    wait_job(added.json()["job_id"])
    return case_id


def _case(client: TestClient, case_id: str) -> dict[str, Any]:
    case: dict[str, Any] = client.get(f"/api/cases/{case_id}").json()["case"]
    return case


def _slots(case: dict[str, Any]) -> dict[str, str | None]:
    return {p["source_name"]: p["slot"] for p in case["photos"]}


def _placement(case: dict[str, Any]) -> dict[str, str]:
    return {p["slot"]: p["id"] for p in case["photos"] if p["slot"] is not None}


def _swapped(client: TestClient, case_id: str) -> dict[str, str]:
    placement = _placement(_case(client, case_id))
    swapped = {
        **placement,
        "upper_right": placement["upper_left"],
        "upper_left": placement["upper_right"],
    }
    response = client.put(f"/api/cases/{case_id}/composite/slots", json={"slots": swapped})
    assert response.status_code == 200, response.text
    assert _placement(response.json()["case"]) == swapped
    assert response.json()["case"]["composite"]["slots_source"] == "manual"
    return swapped


def test_composite_flow(
    client: TestClient, ctx: AppContext, nine: Files, wait_job: WaitJob
) -> None:
    case_id = _composite(client, nine, wait_job)

    assert _slots(_case(client, case_id)) == ROW_MAJOR

    swapped = _swapped(client, case_id)
    [patient] = client.get("/api/patients").json()["patients"]
    # The case card shows the first four cells, in slot order.
    assert patient["cases"][0]["thumbnails"] == [swapped[slot] for slot in SLOTS[:4]]
    cell = swapped["upper_right"]
    flipped = client.patch(f"/api/cases/{case_id}/photos/{cell}", json={"flip": "none"})

    assert flipped.status_code == 200
    [photo] = [p for p in flipped.json()["case"]["photos"] if p["id"] == cell]
    assert (photo["slot"], photo["edit"]["flip"]) == ("upper_right", "none")

    preview = client.get(f"/api/cases/{case_id}/render/composite")

    assert preview.status_code == 200
    assert preview.headers["content-type"] == "image/jpeg"
    with Image.open(io.BytesIO(preview.content)) as decoded:
        assert (decoded.format, decoded.size) == ("JPEG", (1600, 1067))

    started = client.post(f"/api/cases/{case_id}/export", json={})

    assert started.status_code == 200
    wait_job(started.json()["job_id"])
    assert (ctx.library().export_dir(case_id) / COMPOSITE).is_file()
    samples = SampleStore(ctx.dirs.classifier).load()
    assert len(samples.slots) == 9
    assert set(samples.sources) == {"export"}


def test_a_damaged_flip_history_falls_back_to_the_start_flips(
    client: TestClient, ctx: AppContext, nine: Files, wait_job: WaitJob
) -> None:
    damaged = ctx.dirs.classifier / "flips.json"
    damaged.parent.mkdir(parents=True, exist_ok=True)
    damaged.write_bytes(b"{")

    with capture_logs() as logs:
        case_id = _composite(client, nine, wait_job)
        view = client.get(f"/api/cases/{case_id}")

    assert view.status_code == 200
    assert view.json()["flip_defaults"] == DEFAULT_FLIPS
    case = view.json()["case"]
    assert _slots(case) == ROW_MAJOR
    assert {p["slot"]: p["edit"]["flip"] for p in case["photos"]} == DEFAULT_FLIPS
    warnings = [e for e in logs if e["event"] == "flip_history_damaged"]
    assert warnings and all(e["code"] == "storage_error" for e in warnings)
    status = client.get("/api/classifier/status").json()
    assert status["flips_damaged"] == FLIPS_DAMAGED
    assert {slot: f["effective"] for slot, f in status["flips"].items()} == DEFAULT_FLIPS

    started = client.post(f"/api/cases/{case_id}/export", json={})

    learning = wait_job(started.json()["job_id"])["result"]["learning"]
    assert learning == {"error": "failed", "message": FLIPS_DAMAGED}
    assert (ctx.library().export_dir(case_id) / COMPOSITE).is_file()
    assert damaged.read_bytes() == b"{"


def test_reassign_returns_to_automatic_slots(
    client: TestClient, nine: Files, wait_job: WaitJob
) -> None:
    case_id = _composite(client, nine, wait_job)
    _swapped(client, case_id)

    started = client.post(f"/api/cases/{case_id}/composite/assign")

    assert started.status_code == 200
    wait_job(started.json()["job_id"])
    case = _case(client, case_id)
    assert case["composite"]["slots_source"] == "auto"
    assert _slots(case) == ROW_MAJOR


def test_layout_changes_and_deleting_the_baseline(
    client: TestClient, ctx: AppContext, nine: Files, wait_job: WaitJob
) -> None:
    case_id = _composite(client, nine, wait_job)
    before = _case(client, case_id)
    cell = _placement(before)["occlusion_left"]

    changed = client.patch(
        f"/api/cases/{case_id}", json={"composite": {"baseline_photo_id": cell, "gap_px": 8}}
    )

    assert changed.status_code == 200
    block = changed.json()["case"]["composite"]
    # Only the fields sent change; the rest of the block stays as it was.
    assert block == {**before["composite"], "baseline_photo_id": cell, "gap_px": 8}
    nulled = client.patch(f"/api/cases/{case_id}", json={"composite": {"gap_px": None}})
    assert nulled.status_code == 422
    procedure = client.patch(f"/api/cases/{case_id}", json={"output_scale": 2.0})
    assert procedure.status_code == 422

    deleted = client.delete(f"/api/cases/{case_id}/photos/{cell}")

    assert deleted.status_code == 200
    case = deleted.json()["case"]
    assert case["composite"]["baseline_photo_id"] is None
    assert len(case["photos"]) == 8
    assert "occlusion_left" not in _placement(case)
    assert all(p["edit"] is not None for p in case["photos"])
    file = next(p["file"] for p in before["photos"] if p["id"] == cell)
    assert not (ctx.library().case_dir(case_id) / file).exists()
