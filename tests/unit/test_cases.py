"""Changes across both case types: recovery of pending photos at startup, and case views that
stay fast in a large library (spec §4.3, §4.8, §4.11)."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from dental_image_organizer.services import cases, composite_case, procedure_case
from dental_image_organizer.services.context import AppContext
from dental_image_organizer.services.intake import add_photos
from dental_image_organizer.services.jobs import JobProgress
from dental_image_organizer.storage.library import Library, LibraryListing
from support.app import ProcedureSet, make_context, procedure_set, upload, view_set, wait


@pytest.fixture(scope="module")
def photos() -> ProcedureSet:
    return procedure_set()


def _resumed_job(ctx: AppContext, case_id: str) -> str:
    job = ctx.jobs.active_job(case_id)
    assert job is not None
    return job.id


def test_photos_left_pending_by_a_quit_are_processed_at_the_next_start(
    tmp_path: Path, photos: ProcedureSet
) -> None:
    first = make_context(tmp_path)
    release = threading.Event()

    def hold(progress: JobProgress) -> None:
        release.wait(30)

    try:
        library = first.library()
        patient = library.create_patient("Alex Wang", None)
        case_id = library.create_case(patient.id, "procedure", "implant 16").id
        # Holds the case's key, so the jobs of the reference and the upload are still queued
        # when the app quits.
        first.jobs.submit("hold", hold, key=case_id)
        procedure_case.set_reference(first, case_id, upload("ref.jpg", photos.reference))
        procedure_case.upload(first, case_id, [upload("IMG_0001.jpg", photos.intra)])
        assert first.jobs.cancel_queued() == 2
    finally:
        release.set()
        first.close()
    assert {p.state for p in first.library().read_case(case_id).photos} == {"pending"}

    second = make_context(tmp_path)
    try:
        assert cases.resume_pending(second) == 1
        done = wait(second, _resumed_job(second, case_id))

        assert done.kind == "reference"
        case = second.library().read_case(case_id)
        assert [(p.role, p.state, p.edit is not None) for p in case.photos] == [
            ("reference", "ready", True),
            ("photo", "ready", True),
        ]
        assert cases.resume_pending(second) == 0
    finally:
        second.close()


def test_case_views_and_changes_never_scan_the_whole_library(
    ctx: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    library = ctx.library()
    patients = [library.create_patient(f"Patient {i:03d}", None) for i in range(300)]
    known = cases.create_case(ctx, "procedure", "implant 16", patient_id=patients[150].id)
    library.listing()
    scans: list[None] = []
    real_scan = Library._scan

    def counting_scan(self: Library) -> LibraryListing:
        scans.append(None)
        return real_scan(self)

    monkeypatch.setattr(Library, "_scan", counting_scan)

    assert cases.case_details(ctx, known.id).patient.id == patients[150].id
    created = cases.create_case(ctx, "procedure", "crown 26", patient_name="New Patient")
    assert cases.details_of(ctx, created).patient.name == "New Patient"
    # Renames move the index along with the folders.
    cases.change_case(ctx, created.id, title="crown 27")
    cases.update_patient(ctx, created.patient_id, name="Renamed Patient", note=None)
    details = cases.case_details(ctx, created.id)
    assert (details.case.title, details.patient.name) == ("crown 27", "Renamed Patient")
    assert [c.id for c in library.patient(created.patient_id).cases] == [created.id]
    assert library.case_dir(created.id) == library.root / "Renamed Patient" / (
        f"{created.created_at:%Y%m%d} crown 27"
    )
    assert scans == []


def test_a_composite_photo_left_pending_is_processed(ctx: AppContext) -> None:
    library = ctx.library()
    patient = library.create_patient("Alex Wang", None)
    block = composite_case.composite_block(ctx.settings.get())
    case_id = library.create_case(patient.id, "composite", "full mouth", composite=block).id
    [(name, data)] = view_set(count=1)
    # Recorded without the job that processes it, as a crash leaves it.
    (photo_id,), _ = add_photos(ctx, case_id, [upload(name, data)], role="member")

    assert cases.resume_pending(ctx) == 1
    done = wait(ctx, _resumed_job(ctx, case_id))

    assert done.kind == "process"
    photo = library.read_case(case_id).photo(photo_id)
    assert (photo.state, photo.slot) == ("ready", "upper_right")
    assert photo.edit is not None
