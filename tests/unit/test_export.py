"""Case and patient export, conflicts, learning on export, and zips (spec §3.2-§3.4, §4.9)."""

from __future__ import annotations

import zipfile
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from PIL import ExifTags, Image

from dental_image_organizer.core.imageio import RGBImage
from dental_image_organizer.errors import InvalidRequestError, JobNotFoundError
from dental_image_organizer.ml.samples import SampleStore
from dental_image_organizer.services import composite_case, export, procedure_case
from dental_image_organizer.services.context import AppContext
from dental_image_organizer.services.export import ConflictMode, ExportInclude
from dental_image_organizer.storage.library import local_now
from dental_image_organizer.storage.schema import PhotoRecord
from support.app import ProcedureSet, make_context, procedure_set, upload, view_set, wait
from support.fake_model import install_fake_model

PREFIX = "Alex Wang_implant 16"
# Reference, intra, and post photo, named by their capture times.
TAKEN = {
    f"{PREFIX}_20261016_0900.jpg": "2026:10:16 09:00:00",
    f"{PREFIX}_20261016_1000.jpg": "2026:10:16 10:00:00",
    f"{PREFIX}_20261020_1000.jpg": "2026:10:20 10:00:00",
}
SINGLES = list(TAKEN)
COMPOSITE = "Alex Wang_full mouth_Composite_20261016.jpg"

Files = list[tuple[str, bytes]]


@pytest.fixture(scope="module")
def photos() -> ProcedureSet:
    return procedure_set(gps=True)


@pytest.fixture(scope="module")
def nine() -> Files:
    return view_set()


def _today() -> str:
    return f"{local_now():%Y%m%d}"


def _sequence() -> str:
    return f"{PREFIX}_Procedure_{_today()}.jpg"


def _second(name: str) -> str:
    return name.replace(".jpg", "_2.jpg")


def _patient(ctx: AppContext) -> str:
    return ctx.library().create_patient("Alex Wang", None).id


def _with_reference(ctx: AppContext, photos: ProcedureSet, patient_id: str) -> str:
    case_id = ctx.library().create_case(patient_id, "procedure", "implant 16").id
    outcome = procedure_case.set_reference(ctx, case_id, upload("ref.jpg", photos.reference))
    assert outcome.job_id is not None
    wait(ctx, outcome.job_id)
    return case_id


def _upload_photos(ctx: AppContext, case_id: str, photos: ProcedureSet) -> str:
    outcome = procedure_case.upload(
        ctx, case_id, [upload("IMG_0001.jpg", photos.intra), upload("IMG_0002.jpg", photos.post)]
    )
    assert outcome.job_id is not None
    return outcome.job_id


def _procedure(ctx: AppContext, photos: ProcedureSet, patient_id: str | None = None) -> str:
    """A case with the reference and two aligned photos."""
    case_id = _with_reference(ctx, photos, patient_id or _patient(ctx))
    wait(ctx, _upload_photos(ctx, case_id, photos))
    return case_id


def _composite(ctx: AppContext, files: Files, patient_id: str | None = None) -> str:
    library = ctx.library()
    block = composite_case.composite_block(ctx.settings.get())
    case_id = library.create_case(
        patient_id or _patient(ctx), "composite", "full mouth", composite=block
    ).id
    outcome = composite_case.upload(ctx, case_id, [upload(name, data) for name, data in files])
    assert outcome.job_id is not None
    wait(ctx, outcome.job_id)
    return case_id


def _export(
    ctx: AppContext,
    case_id: str,
    conflict: ConflictMode = "keep_both",
    include: list[ExportInclude] | None = None,
) -> dict[str, Any]:
    job_id = export.start_case_export(ctx, case_id, include, conflict)
    return dict(wait(ctx, job_id).result or {})


def _files(result: dict[str, Any]) -> set[str]:
    return {file.removeprefix("export/") for file in result["files"]}


def _on_disk(ctx: AppContext, case_id: str) -> set[str]:
    return {p.name for p in ctx.library().export_dir(case_id).iterdir()}


def test_procedure_export_names_and_metadata(ctx: AppContext, photos: ProcedureSet) -> None:
    case_id = _procedure(ctx, photos)

    result = _export(ctx, case_id)

    assert _files(result) == {*SINGLES, _sequence()}
    assert _on_disk(ctx, case_id) == {*SINGLES, _sequence()}
    assert result["case_id"] == case_id
    assert result["failures"] == []
    assert result["skipped"] == {"pending": 0, "failed": 0}
    assert result["learning"] is None
    folder = ctx.library().export_dir(case_id)
    for name, taken in TAKEN.items():
        with Image.open(folder / name) as image:
            assert image.size == (1200, 800)
            exif = image.getexif()
            assert exif.get_ifd(ExifTags.IFD.Exif)[ExifTags.Base.DateTimeOriginal] == taken
            assert not exif.get_ifd(ExifTags.IFD.GPSInfo)
    records = ctx.library().read_case(case_id).exports
    assert sorted(r.file for r in records) == sorted(result["files"])
    assert sorted(r.kind for r in records) == ["sequence", "single", "single", "single"]


def test_gps_is_kept_when_the_setting_allows(tmp_path: Path, photos: ProcedureSet) -> None:
    ctx = make_context(tmp_path, strip_gps=False)
    try:
        case_id = _procedure(ctx, photos)

        _export(ctx, case_id)

        with Image.open(ctx.library().export_dir(case_id) / SINGLES[1]) as image:
            assert image.getexif().get_ifd(ExifTags.IFD.GPSInfo)
    finally:
        ctx.close()


def test_keep_both_never_overwrites(ctx: AppContext, photos: ProcedureSet) -> None:
    case_id = _procedure(ctx, photos)
    first = _export(ctx, case_id)

    second = _export(ctx, case_id, "keep_both")

    assert _files(second) == {_second(name) for name in _files(first)}
    assert _on_disk(ctx, case_id) == _files(first) | _files(second)
    assert len(ctx.library().read_case(case_id).exports) == 8


def test_replace_deletes_only_files_the_app_wrote(ctx: AppContext, photos: ProcedureSet) -> None:
    case_id = _procedure(ctx, photos)
    theirs = ctx.library().export_dir(case_id) / _sequence()
    theirs.write_bytes(b"the dentist's own file")

    first = _export(ctx, case_id, "keep_both")
    assert _files(first) == {*SINGLES, _second(_sequence())}

    second = _export(ctx, case_id, "replace")

    # Had the first export's files stayed, these names would have taken _2 and _3.
    assert _files(second) == {*SINGLES, _second(_sequence())}
    assert _on_disk(ctx, case_id) == {*SINGLES, _second(_sequence()), _sequence()}
    assert theirs.read_bytes() == b"the dentist's own file"
    records = ctx.library().read_case(case_id).exports
    assert sorted(r.file for r in records) == sorted(second["files"])


def test_exporting_a_composite_again_does_not_count_it_twice(tmp_path: Path, nine: Files) -> None:
    ctx = make_context(tmp_path, model_spec=install_fake_model(tmp_path / "cache" / "models"))
    try:
        case_id = _composite(ctx, nine)
        first = _export(ctx, case_id)
        assert first["learning"] == {"samples": 9, "trained": False}
        cell = next(p for p in ctx.library().read_case(case_id).photos if p.slot == "upper_right")
        composite_case.set_flip(ctx, case_id, cell.id, "none")

        second = _export(ctx, case_id, "replace")

        assert _files(second) == {COMPOSITE}
        assert _on_disk(ctx, case_id) == {COMPOSITE}
        samples = SampleStore(ctx.dirs.classifier).load()
        assert len(samples.slots) == 9
        assert set(samples.sources) == {"export"}
        history = ctx.ai.flip_history()
        assert len(history) == 9
        assert {c.slot: c.flip for c in history}["upper_right"] == "none"
    finally:
        ctx.close()


def test_composite_export_without_the_model_still_learns_flips(
    ctx: AppContext, nine: Files
) -> None:
    case_id = _composite(ctx, nine)

    result = _export(ctx, case_id)

    assert result["learning"] == {"error": "no_model", "message": None}
    assert len(ctx.ai.flip_history()) == 9
    assert _on_disk(ctx, case_id) == {COMPOSITE}
    assert [r.kind for r in ctx.library().read_case(case_id).exports] == ["composite"]


def test_an_export_waits_for_processing_of_its_case(ctx: AppContext, photos: ProcedureSet) -> None:
    case_id = _with_reference(ctx, photos, _patient(ctx))
    _upload_photos(ctx, case_id, photos)

    result = _export(ctx, case_id)

    assert result["skipped"] == {"pending": 0, "failed": 0}
    assert {SINGLES[1], SINGLES[2]} <= _files(result)


def test_a_photo_that_fails_to_render_does_not_stop_the_export(
    ctx: AppContext, photos: ProcedureSet, monkeypatch: pytest.MonkeyPatch
) -> None:
    case_id = _procedure(ctx, photos)
    broken = next(
        p for p in ctx.library().read_case(case_id).photos if p.source_name == "IMG_0001.jpg"
    )
    real = export.render_full

    def render_full(photo: PhotoRecord, rgb: RGBImage) -> RGBImage:
        if photo.id == broken.id:
            raise RuntimeError("warp failed")
        return real(photo, rgb)

    monkeypatch.setattr(export, "render_full", render_full)

    result = _export(ctx, case_id)

    assert result["failures"] == [
        {"photo_id": broken.id, "message": "Unexpected RuntimeError: warp failed"}
    ]
    assert _files(result) == {SINGLES[0], SINGLES[2], _sequence()}
    assert not any(name.endswith(".part") for name in _on_disk(ctx, case_id))


def test_patient_export_isolates_failing_cases(
    ctx: AppContext, photos: ProcedureSet, nine: Files, monkeypatch: pytest.MonkeyPatch
) -> None:
    patient_id = _patient(ctx)
    procedure_id = _procedure(ctx, photos, patient_id)
    composite_id = _composite(ctx, nine, patient_id)
    third = ctx.library().create_case(patient_id, "procedure", "crown 26").id
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

    job_id = export.start_patient_export(ctx, patient_id, None, "keep_both")
    result = dict(wait(ctx, job_id).result or {})

    assert result["patient_id"] == patient_id
    assert result["failed_cases"] == [
        {"case_id": third, "message": "Unexpected RuntimeError: disk on fire"}
    ]
    assert {c["case_id"] for c in result["cases"]} == {procedure_id, composite_id}
    path, name = export.export_zip(ctx, job_id, patient_id=patient_id)
    assert name == f"Alex Wang_Export_{_today()}.zip"
    procedure_folder = ctx.library().case_dir(procedure_id).name
    composite_folder = ctx.library().case_dir(composite_id).name
    with zipfile.ZipFile(path) as zf:
        assert set(zf.namelist()) == {
            *(f"{procedure_folder}/{n}" for n in (*SINGLES, _sequence())),
            f"{composite_folder}/{COMPOSITE}",
        }


def test_zip_downloads(ctx: AppContext, photos: ProcedureSet) -> None:
    patient_id = _patient(ctx)
    case_id = _procedure(ctx, photos, patient_id)
    other = ctx.library().create_case(patient_id, "procedure", "crown 26").id
    job_id = export.start_case_export(ctx, case_id, None, "keep_both")
    wait(ctx, job_id)
    folder = ctx.library().export_dir(case_id)

    path, name = export.export_zip(ctx, job_id, case_id=case_id)

    assert name == f"{PREFIX}_Export_{_today()}.zip"
    assert path.parent == ctx.dirs.exports
    assert path.name.startswith(f"{job_id}-")
    with zipfile.ZipFile(path) as zf:
        assert set(zf.namelist()) == {*SINGLES, _sequence()}
        assert {info.compress_type for info in zf.infolist()} == {zipfile.ZIP_STORED}
        assert zf.read(SINGLES[0]) == (folder / SINGLES[0]).read_bytes()
    with pytest.raises(JobNotFoundError):
        export.export_zip(ctx, job_id, case_id=other)
    with pytest.raises(JobNotFoundError):
        export.export_zip(ctx, job_id, patient_id=patient_id)

    (folder / SINGLES[0]).unlink()
    path, _ = export.export_zip(ctx, job_id, case_id=case_id)

    with zipfile.ZipFile(path) as zf:
        assert set(zf.namelist()) == {*SINGLES[1:], _sequence()}


def test_each_zip_of_an_export_has_its_own_file(ctx: AppContext, photos: ProcedureSet) -> None:
    case_id = _procedure(ctx, photos)
    job_id = export.start_case_export(ctx, case_id, None, "keep_both")
    wait(ctx, job_id)

    first, _ = export.export_zip(ctx, job_id, case_id=case_id)
    second, _ = export.export_zip(ctx, job_id, case_id=case_id)

    # A double-clicked Download deletes one zip after sending it; the other must stay intact.
    assert first != second
    first.unlink()
    with zipfile.ZipFile(second) as zf:
        assert set(zf.namelist()) == {*SINGLES, _sequence()}


def test_include_must_fit_the_case_type(ctx: AppContext) -> None:
    patient_id = _patient(ctx)
    case_id = ctx.library().create_case(patient_id, "procedure", "implant 16").id

    for include in (["composite"], ["singles", "composite"], []):
        with pytest.raises(InvalidRequestError):
            export.start_case_export(ctx, case_id, include, "keep_both")
