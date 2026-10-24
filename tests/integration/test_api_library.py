"""Patients, cases, uploads, and case archives over the API (spec §3.1, §4.11, §4.12, §8.2
item 5)."""

from __future__ import annotations

import io
import threading
import zipfile
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import numpy as np
import pillow_heif
from fastapi.testclient import TestClient
from httpx import Response
from PIL import Image
from structlog.testing import capture_logs

from dental_image_organizer.api.app import create_app
from dental_image_organizer.core.slots import DEFAULT_FLIPS
from dental_image_organizer.services.context import AppContext
from dental_image_organizer.services.jobs import JobProgress
from dental_image_organizer.storage.library import local_now
from support.app import ORIGIN, WAIT_SECONDS, jpeg, make_context

WaitJob = Callable[[str | None], dict[str, Any]]

VIDEO = b"\x00\x00\x00\x14ftypqt  \x00\x00\x00\x00qt  " + bytes(1000)
SIDECAR = b'<?xml version="1.0" encoding="UTF-8"?><plist version="1.0"/>'
RAW = b"II*\x00" + bytes(60)
SUMMARY_FIELDS = ("id", "type", "title", "photos", "needs_review", "pending", "failed")
MODIFIED = datetime(2026, 10, 15, 9, 30)


def _pixels(seed: int) -> np.ndarray:
    return np.random.default_rng(seed).integers(0, 256, (48, 64, 3), dtype=np.uint8)


def _photo(seed: int = 1) -> bytes:
    return jpeg(_pixels(seed), "2026:10:16 10:00:00")


def _heic(seed: int = 2) -> bytes:
    buf = io.BytesIO()
    pillow_heif.from_pillow(Image.fromarray(_pixels(seed))).save(buf, quality=90)
    return buf.getvalue()


def _ms(when: datetime) -> str:
    return str(round(when.timestamp() * 1000))


def _create(
    client: TestClient, kind: str = "procedure", title: str = "implant 16", **patient: str
) -> dict[str, Any]:
    body = {"type": kind, "title": title, **(patient or {"patient_name": "Alex Wang"})}
    response = client.post("/api/cases", json=body)
    assert response.status_code == 201, response.text
    view: dict[str, Any] = response.json()
    return view


def _upload(client: TestClient, case_id: str, *photos: tuple[str, bytes]) -> dict[str, Any]:
    files = [("files", (name, data, "image/jpeg")) for name, data in photos]
    response = client.post(f"/api/cases/{case_id}/photos", files=files)
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


def _error(response: Response) -> dict[str, Any]:
    error: dict[str, Any] = response.json()["error"]
    return error


def _held(release: threading.Event) -> Callable[[JobProgress], None]:
    def job(progress: JobProgress) -> None:
        release.wait(WAIT_SECONDS)

    return job


def test_patients_and_cases_lifecycle(client: TestClient, ctx: AppContext) -> None:
    created = _create(client)
    case_id, patient_id = created["case"]["id"], created["patient"]["id"]

    assert created["case"]["type"] == "procedure"
    assert created["patient"]["name"] == "Alex Wang"
    assert created["suggested_output_scale"] is None
    assert created["flip_defaults"] is None
    listing = client.get("/api/patients")
    assert listing.status_code == 200
    assert listing.json()["problems"] == []
    [patient] = listing.json()["patients"]
    assert (patient["id"], patient["name"], patient["note"]) == (patient_id, "Alex Wang", None)
    [summary] = patient["cases"]
    assert {field: summary[field] for field in SUMMARY_FIELDS} == {
        "id": case_id,
        "type": "procedure",
        "title": "implant 16",
        "photos": 0,
        "needs_review": 0,
        "pending": 0,
        "failed": 0,
    }
    assert summary["thumbnails"] == []

    second = _create(client, "composite", "full mouth", patient_id=patient_id)

    assert second["patient"]["id"] == patient_id
    assert second["case"]["composite"]["slots_source"] == "auto"
    assert second["flip_defaults"] == DEFAULT_FLIPS
    assert second["suggested_output_scale"] is None

    noted = client.patch(f"/api/patients/{patient_id}", json={"note": "chart 42"})

    assert noted.status_code == 200
    assert (noted.json()["name"], noted.json()["note"]) == ("Alex Wang", "chart 42")
    assert {c["id"] for c in noted.json()["cases"]} == {case_id, second["case"]["id"]}

    folder = ctx.library().case_dir(case_id)
    renamed = client.patch(f"/api/cases/{case_id}", json={"title": "implant 26"})

    assert renamed.status_code == 200
    assert renamed.json()["case"]["title"] == "implant 26"
    moved = ctx.library().case_dir(case_id)
    assert moved.name == f"{folder.name[:8]} implant 26"
    assert not folder.exists()
    assert client.get(f"/api/cases/{case_id}").json()["case"]["title"] == "implant 26"

    deleted = client.delete(f"/api/cases/{case_id}")

    assert deleted.status_code == 204
    gone = client.get(f"/api/cases/{case_id}")
    assert gone.status_code == 404
    assert _error(gone)["code"] == "case_not_found"
    trashed = ctx.library().root / ".trash" / moved.parent.name / moved.name
    assert (trashed / "case.json").is_file()


def test_creating_a_case_needs_exactly_one_patient(client: TestClient) -> None:
    patient_id = _create(client)["patient"]["id"]

    both = client.post(
        "/api/cases",
        json={
            "type": "procedure",
            "title": "crown 26",
            "patient_id": patient_id,
            "patient_name": "Alex Wang",
        },
    )
    neither = client.post("/api/cases", json={"type": "procedure", "title": "crown 26"})
    # A note is given with a new patient only; for an existing one it would be lost.
    noted = client.post(
        "/api/cases",
        json={
            "type": "procedure",
            "title": "crown 26",
            "patient_id": patient_id,
            "patient_note": "chart 42",
        },
    )

    for response in (both, neither, noted):
        assert response.status_code == 422
        assert _error(response)["code"] == "invalid_request"
    [patient] = client.get("/api/patients").json()["patients"]
    assert len(patient["cases"]) == 1
    assert patient["note"] is None


def test_a_case_that_cannot_be_created_leaves_no_new_patient(
    client: TestClient, ctx: AppContext
) -> None:
    response = client.post(
        "/api/cases", json={"type": "procedure", "title": "///", "patient_name": "Alex Wang"}
    )

    assert response.status_code == 422
    assert _error(response)["code"] == "invalid_name"
    assert client.get("/api/patients").json() == {"patients": [], "problems": []}
    assert list(ctx.library().root.iterdir()) == []


def test_mixed_uploads_store_only_photos(
    client: TestClient, ctx: AppContext, wait_job: WaitJob
) -> None:
    case_id = _create(client)["case"]["id"]
    files = [
        ("files", ("IMG_0001.JPG", _photo(), "image/jpeg")),
        ("files", ("IMG_0002.HEIC", _heic(), "image/heic")),
        ("files", ("IMG_0003.MOV", VIDEO, "video/quicktime")),
        ("files", ("IMG_0002.AAE", SIDECAR, "application/xml")),
        ("files", ("IMG_0004.DNG", RAW, "image/x-adobe-dng")),
    ]

    response = client.post(
        f"/api/cases/{case_id}/photos", files=files, data={"last_modified": [_ms(MODIFIED)] * 5}
    )

    assert response.status_code == 200
    result = response.json()
    assert len(result["added"]) == 2
    assert [(s["name"], s["reason"]) for s in result["skipped"]] == [
        ("IMG_0003.MOV", "video"),
        ("IMG_0002.AAE", "sidecar"),
        ("IMG_0004.DNG", "raw"),
    ]
    assert all(s["message"] for s in result["skipped"])
    wait_job(result["job_id"])
    case = client.get(f"/api/cases/{case_id}").json()["case"]
    photos = {p["source_name"]: p for p in case["photos"]}
    assert sorted(p["id"] for p in photos.values()) == sorted(result["added"])
    assert {name: p["state"] for name, p in photos.items()} == {
        "IMG_0001.JPG": "ready",
        "IMG_0002.HEIC": "ready",
    }
    # Without an EXIF time, the browser's modification time stands, as local wall-clock time.
    heic = photos["IMG_0002.HEIC"]
    assert (heic["captured_at"], heic["captured_at_source"]) == (
        MODIFIED.isoformat(),
        "file_mtime",
    )
    assert photos["IMG_0001.JPG"]["captured_at_source"] == "exif"
    originals = ctx.library().case_dir(case_id) / "originals"
    assert len(list(originals.iterdir())) == 2


def test_last_modified_must_match_the_files(client: TestClient) -> None:
    case_id = _create(client)["case"]["id"]
    files = [
        ("files", ("IMG_0001.jpg", _photo(1), "image/jpeg")),
        ("files", ("IMG_0002.jpg", _photo(2), "image/jpeg")),
    ]

    response = client.post(
        f"/api/cases/{case_id}/photos", files=files, data={"last_modified": _ms(MODIFIED)}
    )

    assert response.status_code == 422
    assert _error(response)["code"] == "invalid_request"
    assert client.get(f"/api/cases/{case_id}").json()["case"]["photos"] == []


def test_case_archive_round_trip(
    client: TestClient, ctx: AppContext, tmp_path: Path, wait_job: WaitJob
) -> None:
    created = _create(client)
    case_id, patient_id = created["case"]["id"], created["patient"]["id"]
    wait_job(_upload(client, case_id, ("IMG_0001.jpg", _photo()))["job_id"])
    [photo] = client.get(f"/api/cases/{case_id}").json()["case"]["photos"]

    download = client.get(f"/api/cases/{case_id}/archive")

    assert download.status_code == 200
    assert download.headers["content-type"] == "application/zip"
    name = f"Alex Wang_implant 16_Archive_{local_now():%Y%m%d}.zip"
    assert download.headers["content-disposition"] == f"attachment; filename*=utf-8''{quote(name)}"
    with zipfile.ZipFile(io.BytesIO(download.content)) as zf:
        assert set(zf.namelist()) == {"patient.json", "case.json", photo["file"]}
    assert list(ctx.dirs.exports.iterdir()) == []

    other = make_context(tmp_path / "other")
    try:
        app = create_app(other, port=8765, shutdown=lambda: None, web_dir=None)
        with TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN}) as second:

            def send(query: str = "") -> Response:
                archive = {"file": ("archive.zip", download.content, "application/zip")}
                return second.post(f"/api/archive/import{query}", files=archive)

            imported = send()
            again = send()
            copied = send("?on_conflict=copy")
            skipped = send("?on_conflict=skip")
            listing = second.get("/api/patients").json()
        leftovers = list(other.dirs.exports.iterdir())
    finally:
        other.close()

    assert imported.status_code == 201
    assert imported.json() == {
        "status": "imported",
        "case_id": case_id,
        "patient_id": patient_id,
        "title": "implant 16",
    }
    assert again.status_code == 409
    assert _error(again)["code"] == "case_exists"
    assert _error(again)["details"] == {"case_id": case_id, "title": "implant 16"}
    assert copied.status_code == 201
    assert copied.json()["status"] == "imported"
    assert copied.json()["case_id"] not in {case_id, None}
    assert skipped.status_code == 200
    assert skipped.json()["status"] == "skipped"
    [patient] = listing["patients"]
    assert {c["id"] for c in patient["cases"]} == {case_id, copied.json()["case_id"]}
    assert leftovers == []


def test_damaged_library_entries_are_listed_as_problems(
    client: TestClient, ctx: AppContext
) -> None:
    case_id = _create(client)["case"]["id"]
    folder = ctx.library().case_dir(case_id)
    (folder / "case.json").write_text("{not json", encoding="utf-8")

    response = client.get("/api/patients")

    assert response.status_code == 200
    [patient] = response.json()["patients"]
    assert patient["cases"] == []
    assert response.json()["problems"] == [
        {
            "folder": f"{folder.parent.name}/{folder.name}",
            "message": "The file is damaged: it is not valid JSON.",
        }
    ]


def test_renaming_and_deleting_wait_for_jobs(client: TestClient, ctx: AppContext) -> None:
    created = _create(client)
    case_id, patient_id = created["case"]["id"], created["patient"]["id"]
    release = threading.Event()
    job = ctx.jobs.submit("hold", _held(release), key=case_id)
    try:
        renamed = client.patch(f"/api/cases/{case_id}", json={"title": "implant 26"})
        deleted = client.delete(f"/api/cases/{case_id}")
        patient_renamed = client.patch(f"/api/patients/{patient_id}", json={"name": "Alex Wong"})
        noted = client.patch(f"/api/patients/{patient_id}", json={"note": "chart 42"})
        same_title = client.patch(f"/api/cases/{case_id}", json={"title": "implant 16"})
    finally:
        release.set()
    ctx.jobs.wait(job.id, WAIT_SECONDS)

    for response in (renamed, deleted, patient_renamed):
        assert response.status_code == 409
        assert _error(response)["code"] == "job_running"
    # Only a rename can leave an export's file names stale; a note can change meanwhile.
    assert noted.status_code == 200
    assert same_title.status_code == 200
    view = client.get(f"/api/cases/{case_id}").json()
    assert view["case"]["title"] == "implant 16"
    assert (view["patient"]["name"], view["patient"]["note"]) == ("Alex Wang", "chart 42")


def test_library_changes_never_log_names(client: TestClient, wait_job: WaitJob) -> None:
    with capture_logs() as logs:
        created = _create(client)
        case_id, patient_id = created["case"]["id"], created["patient"]["id"]
        added = _upload(client, case_id, ("Alex Wang smile.jpg", _photo()))
        wait_job(added["job_id"])
        client.patch(f"/api/cases/{case_id}", json={"title": "implant 26"})
        client.patch(f"/api/patients/{patient_id}", json={"name": "Alex Wong", "note": "chart"})
        client.get(f"/api/cases/{case_id}/archive")
        client.delete(f"/api/cases/{case_id}/photos/{added['added'][0]}")
        client.delete(f"/api/cases/{case_id}")
        refused = client.get(f"/api/cases/{case_id}")

    assert refused.status_code == 404
    assert {"case_created", "case_renamed", "photo_deleted", "case_trashed"} <= {
        e["event"] for e in logs
    }
    text = repr(logs)
    for private in ("Alex", "implant", "chart", "smile"):
        assert private not in text


def test_photo_changes_check_the_case_type(client: TestClient, wait_job: WaitJob) -> None:
    case_id = _create(client)["case"]["id"]
    added = _upload(client, case_id, ("IMG_0001.jpg", _photo()))
    wait_job(added["job_id"])
    [photo_id] = added["added"]

    response = client.patch(f"/api/cases/{case_id}/photos/{photo_id}", json={"slot": "upper_right"})

    assert response.status_code == 422
    assert _error(response) == {
        "code": "invalid_request",
        "message": "This is a procedure case.",
        "details": {},
    }
