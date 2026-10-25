"""A procedure case over the API: reference, photos, stages, edits, images, and export (spec
§3.2, §8.2 item 1)."""

from __future__ import annotations

import io
import threading
import zipfile
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import numpy as np
import pytest
from fastapi.testclient import TestClient
from httpx import Response
from PIL import Image

from dental_image_organizer.services.context import AppContext
from dental_image_organizer.services.jobs import JobProgress
from dental_image_organizer.storage.library import local_now
from dental_image_organizer.storage.schema import new_id
from support.app import WAIT_SECONDS, ProcedureSet, jpeg, procedure_set
from support.synthetic import make_scene

WaitJob = Callable[[str | None], dict[str, Any]]

PREFIX = "Alex Wang_implant 16"
SINGLES = {
    f"{PREFIX}_20261016_0900.jpg",
    f"{PREFIX}_20261016_1000.jpg",
    f"{PREFIX}_20261020_1000.jpg",
}
SIZES = {"thumb": (320, 213), "preview": (1200, 800), "full": (1200, 800)}


@pytest.fixture(scope="module")
def photos() -> ProcedureSet:
    return procedure_set()


def _today() -> str:
    return f"{local_now():%Y%m%d}"


def _error(response: Response) -> dict[str, Any]:
    error: dict[str, Any] = response.json()["error"]
    return error


def _create(client: TestClient) -> str:
    body = {"type": "procedure", "title": "implant 16", "patient_name": "Alex Wang"}
    response = client.post("/api/cases", json=body)
    assert response.status_code == 201, response.text
    case_id: str = response.json()["case"]["id"]
    return case_id


def _set_reference(client: TestClient, case_id: str, photos: ProcedureSet) -> dict[str, Any]:
    response = client.put(
        f"/api/cases/{case_id}/reference",
        files={"file": ("ref.jpg", photos.reference, "image/jpeg")},
    )
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


def _upload(client: TestClient, case_id: str, *photos: tuple[str, bytes]) -> dict[str, Any]:
    files = [("files", (name, data, "image/jpeg")) for name, data in photos]
    response = client.post(f"/api/cases/{case_id}/photos", files=files)
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


def _aligned(client: TestClient, photos: ProcedureSet, wait_job: WaitJob) -> str:
    """A case with the reference and the intra and post photos, all processed."""
    case_id = _create(client)
    wait_job(_set_reference(client, case_id, photos)["job_id"])
    added = _upload(client, case_id, ("IMG_0001.jpg", photos.intra), ("IMG_0002.jpg", photos.post))
    wait_job(added["job_id"])
    return case_id


def _by_name(view: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {p["source_name"]: p for p in view["case"]["photos"]}


def _held(release: threading.Event) -> Callable[[JobProgress], None]:
    def job(progress: JobProgress) -> None:
        release.wait(WAIT_SECONDS)

    return job


def test_procedure_flow(
    client: TestClient, ctx: AppContext, photos: ProcedureSet, wait_job: WaitJob
) -> None:
    case_id = _aligned(client, photos, wait_job)

    named = _by_name(client.get(f"/api/cases/{case_id}").json())

    assert {name: p["edit"]["status"] for name, p in named.items()} == {
        "ref.jpg": "ok",
        "IMG_0001.jpg": "ok",
        "IMG_0002.jpg": "ok",
    }
    assert {name: p["stage"] for name, p in named.items()} == {
        "ref.jpg": "pre_op",
        "IMG_0001.jpg": "intra_op",
        "IMG_0002.jpg": "post_op",
    }
    intra, post = named["IMG_0001.jpg"]["id"], named["IMG_0002.jpg"]["id"]

    staged = client.patch(f"/api/cases/{case_id}/photos/{post}", json={"stage": "intra_op"})

    assert staged.status_code == 200
    restaged = _by_name(staged.json())["IMG_0002.jpg"]
    assert (restaged["stage"], restaged["stage_source"]) == ("intra_op", "manual")

    edit = {"transform": list(photos.truth), "model": "similarity"}
    edited = client.patch(f"/api/cases/{case_id}/photos/{intra}", json={"edit": edit})

    assert edited.status_code == 200
    manual = _by_name(edited.json())["IMG_0001.jpg"]["edit"]
    assert manual["source"] == "manual"
    assert manual["transform"] == list(photos.truth)

    for size, expected in SIZES.items():
        image = client.get(f"/api/cases/{case_id}/photos/{intra}/image", params={"size": size})
        assert image.status_code == 200
        assert image.headers["content-type"] == "image/jpeg"
        assert image.headers["cache-control"] == "no-cache"
        with Image.open(io.BytesIO(image.content)) as decoded:
            assert (decoded.format, decoded.size) == ("JPEG", expected)

    sequence = client.get(f"/api/cases/{case_id}/render/sequence")

    assert sequence.status_code == 200
    assert sequence.headers["content-type"] == "image/jpeg"
    with Image.open(io.BytesIO(sequence.content)) as decoded:
        assert decoded.format == "JPEG"

    started = client.post(f"/api/cases/{case_id}/export", json={})

    assert started.status_code == 200
    job_id = started.json()["job_id"]
    result = wait_job(job_id)["result"]
    names = {file.removeprefix("export/") for file in result["files"]}
    assert names == {*SINGLES, f"{PREFIX}_Procedure_{_today()}.jpg"}

    download = client.get(f"/api/cases/{case_id}/export/{job_id}/zip")

    assert download.status_code == 200
    assert download.headers["content-type"] == "application/zip"
    zip_name = quote(f"{PREFIX}_Export_{_today()}.zip")
    assert download.headers["content-disposition"] == f"attachment; filename*=utf-8''{zip_name}"
    with zipfile.ZipFile(io.BytesIO(download.content)) as zf:
        assert set(zf.namelist()) == names
    assert list(ctx.dirs.exports.iterdir()) == []


def test_deleting_a_photo_removes_its_record_and_original(
    client: TestClient, ctx: AppContext, photos: ProcedureSet, wait_job: WaitJob
) -> None:
    case_id = _aligned(client, photos, wait_job)
    named = _by_name(client.get(f"/api/cases/{case_id}").json())
    reference, intra, post = (named[n] for n in ("ref.jpg", "IMG_0001.jpg", "IMG_0002.jpg"))
    selection = {"photo_ids": [reference["id"], intra["id"], post["id"]]}
    assert client.patch(f"/api/cases/{case_id}", json={"sequence": selection}).status_code == 200
    folder = ctx.library().case_dir(case_id)

    response = client.delete(f"/api/cases/{case_id}/photos/{intra['id']}")

    assert response.status_code == 200
    case = response.json()["case"]
    assert {p["id"] for p in case["photos"]} == {reference["id"], post["id"]}
    assert case["sequence"]["photo_ids"] == [reference["id"], post["id"]]
    # The post photo's day is now the earliest, so the stage rule makes it intra-op (§3.2).
    assert _by_name(response.json())["IMG_0002.jpg"]["stage"] == "intra_op"
    assert not (folder / intra["file"]).exists()
    assert (folder / reference["file"]).is_file()
    assert (folder / post["file"]).is_file()
    again = client.delete(f"/api/cases/{case_id}/photos/{intra['id']}")
    assert again.status_code == 404
    assert _error(again)["code"] == "photo_not_found"


def test_the_case_card_counts_photos_and_shows_the_reference_first(
    client: TestClient, photos: ProcedureSet, wait_job: WaitJob
) -> None:
    case_id = _create(client)
    with Image.open(io.BytesIO(photos.reference)) as image:
        # Taken after the other photos, so capture order alone would put it last.
        later = jpeg(np.asarray(image.convert("RGB")), "2026:10:21 09:00:00")
    reference = client.put(
        f"/api/cases/{case_id}/reference", files={"file": ("ref.jpg", later, "image/jpeg")}
    )
    wait_job(reference.json()["job_id"])
    unrelated = jpeg(make_scene(99), "2026:10:17 10:00:00")
    added = _upload(
        client,
        case_id,
        ("IMG_0001.jpg", photos.intra),
        ("IMG_0002.jpg", unrelated),
        ("IMG_0003.jpg", photos.post[:300]),
    )
    wait_job(added["job_id"])
    intra, other, _ = added["added"]

    [patient] = client.get("/api/patients").json()["patients"]

    [summary] = patient["cases"]
    assert (summary["photos"], summary["needs_review"], summary["pending"], summary["failed"]) == (
        4,
        1,
        0,
        1,
    )
    # The broken photo is not ready, so it has no thumbnail.
    assert summary["thumbnails"] == [reference.json()["added"][0], intra, other]


def test_photo_patch_needs_exactly_one_change(client: TestClient) -> None:
    case_id = _create(client)

    for body in ({}, {"stage": "post_op", "reset": True}):
        # A body that passed would meet an unknown photo and answer 404.
        response = client.patch(f"/api/cases/{case_id}/photos/{new_id()}", json=body)

        assert response.status_code == 422
        assert _error(response)["code"] == "invalid_request"


def test_deleting_the_reference_is_refused(
    client: TestClient, photos: ProcedureSet, wait_job: WaitJob
) -> None:
    case_id = _create(client)
    reference = _set_reference(client, case_id, photos)
    wait_job(reference["job_id"])
    [reference_id] = reference["added"]

    response = client.delete(f"/api/cases/{case_id}/photos/{reference_id}")

    assert response.status_code == 422
    assert _error(response)["message"] == "Replace the reference instead of deleting it."
    case = client.get(f"/api/cases/{case_id}").json()["case"]
    assert [p["id"] for p in case["photos"]] == [reference_id]


def test_an_unprocessed_image_is_not_ready(
    client: TestClient, ctx: AppContext, photos: ProcedureSet, wait_job: WaitJob
) -> None:
    case_id = _create(client)
    release = threading.Event()
    # Holding the case's job key keeps the upload's processing job queued.
    ctx.jobs.submit("hold", _held(release), key=case_id)
    try:
        added = _upload(client, case_id, ("IMG_0001.jpg", photos.intra))
        [photo_id] = added["added"]
        processed = client.get(f"/api/cases/{case_id}/photos/{photo_id}/image")
        original = client.get(
            f"/api/cases/{case_id}/photos/{photo_id}/image",
            params={"variant": "original", "size": "thumb"},
        )
    finally:
        release.set()
    wait_job(added["job_id"])

    assert processed.status_code == 409
    assert _error(processed)["code"] == "not_ready"
    assert original.status_code == 200
    with Image.open(io.BytesIO(original.content)) as decoded:
        assert max(decoded.size) == 320
