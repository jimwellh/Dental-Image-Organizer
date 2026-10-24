"""Patient export over the API: every case of a patient in one zip (spec §3.4, §8.2 item 3)."""

from __future__ import annotations

import io
import zipfile
from collections.abc import Callable
from datetime import date
from typing import Any
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from dental_image_organizer.services import export
from dental_image_organizer.services.context import AppContext
from dental_image_organizer.services.export import ConflictMode
from dental_image_organizer.storage.library import local_now
from support.app import procedure_set, view_set

WaitJob = Callable[[str | None], dict[str, Any]]

PREFIX = "Alex Wang_implant 16"
SINGLES = (
    f"{PREFIX}_20261016_0900.jpg",
    f"{PREFIX}_20261016_1000.jpg",
    f"{PREFIX}_20261020_1000.jpg",
)
COMPOSITE = "Alex Wang_full mouth_Composite_20261016.jpg"


def _create(client: TestClient, kind: str, title: str, **patient: str) -> dict[str, Any]:
    response = client.post("/api/cases", json={"type": kind, "title": title, **patient})
    assert response.status_code == 201, response.text
    view: dict[str, Any] = response.json()
    return view


def _upload(
    client: TestClient, wait_job: WaitJob, case_id: str, photos: list[tuple[str, bytes]]
) -> None:
    files = [("files", (name, data, "image/jpeg")) for name, data in photos]
    response = client.post(f"/api/cases/{case_id}/photos", files=files)
    assert response.status_code == 200, response.text
    wait_job(response.json()["job_id"])


def test_patient_export_across_case_types(
    client: TestClient, ctx: AppContext, wait_job: WaitJob, monkeypatch: pytest.MonkeyPatch
) -> None:
    photos = procedure_set()
    created = _create(client, "procedure", "implant 16", patient_name="Alex Wang")
    patient_id, procedure_id = created["patient"]["id"], created["case"]["id"]
    reference = client.put(
        f"/api/cases/{procedure_id}/reference",
        files={"file": ("ref.jpg", photos.reference, "image/jpeg")},
    )
    wait_job(reference.json()["job_id"])
    _upload(
        client,
        wait_job,
        procedure_id,
        [("IMG_0001.jpg", photos.intra), ("IMG_0002.jpg", photos.post)],
    )
    composite_id = _create(client, "composite", "full mouth", patient_id=patient_id)["case"]["id"]
    _upload(client, wait_job, composite_id, view_set())
    third = _create(client, "procedure", "crown 26", patient_id=patient_id)["case"]["id"]
    real = export.export_case

    def export_case(
        ctx: AppContext,
        case_id: str,
        include: Any,
        conflict: ConflictMode,
        *,
        today: date | None = None,
    ) -> dict[str, Any]:
        if case_id == third:
            raise RuntimeError("disk on fire")
        return real(ctx, case_id, include, conflict, today=today)

    monkeypatch.setattr(export, "export_case", export_case)

    started = client.post(f"/api/patients/{patient_id}/export", json={})

    assert started.status_code == 200
    job_id = started.json()["job_id"]
    result = wait_job(job_id)["result"]
    assert result["failed_cases"] == [
        {"case_id": third, "message": "Unexpected RuntimeError: disk on fire"}
    ]

    download = client.get(f"/api/patients/{patient_id}/export/{job_id}/zip")

    assert download.status_code == 200
    assert download.headers["content-type"] == "application/zip"
    name = quote(f"Alex Wang_Export_{local_now():%Y%m%d}.zip")
    assert download.headers["content-disposition"] == f"attachment; filename*=utf-8''{name}"
    procedure_folder = ctx.library().case_dir(procedure_id).name
    composite_folder = ctx.library().case_dir(composite_id).name
    sequence = f"{PREFIX}_Procedure_{local_now():%Y%m%d}.jpg"
    with zipfile.ZipFile(io.BytesIO(download.content)) as zf:
        assert set(zf.namelist()) == {
            *(f"{procedure_folder}/{n}" for n in (*SINGLES, sequence)),
            f"{composite_folder}/{COMPOSITE}",
        }
        exported = ctx.library().export_dir(composite_id) / COMPOSITE
        assert zf.read(f"{composite_folder}/{COMPOSITE}") == exported.read_bytes()
    assert list(ctx.dirs.exports.iterdir()) == []
    # A download for the wrong patient or a case export id finds no such export.
    wrong = client.get(f"/api/cases/{procedure_id}/export/{job_id}/zip")
    assert wrong.status_code == 404
    assert wrong.json()["error"]["code"] == "job_not_found"
