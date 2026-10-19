import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dental_image_organizer.cli import app
from dental_image_organizer.config import Settings, SettingsStore
from dental_image_organizer.instance import InstanceLock
from dental_image_organizer.paths import app_dirs
from dental_image_organizer.storage.library import CaseLocks, Library
from dental_image_organizer.storage.schema import PhotoRecord, new_id

NOW = datetime(2026, 10, 16, 14, 30, tzinfo=timezone(timedelta(hours=8)))
DATA = b"\xff\xd8an original\xff\xd9"


@pytest.fixture(autouse=True)
def app_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DIO_APP_DIR", str(tmp_path / "app"))
    return tmp_path / "app"


def make_case_folder(root: Path) -> Path:
    lib = Library(root, CaseLocks(), clock=lambda: NOW)
    patient = lib.create_patient("Alex Wang", None)
    case = lib.create_case(patient.id, "procedure", "implant 16")
    sha = hashlib.sha256(DATA).hexdigest()
    rec = PhotoRecord.model_validate(
        {
            "id": new_id(),
            "sha256": sha,
            "file": f"originals/{sha[:16]}.jpg",
            "source_name": "x.jpg",
            "role": "reference",
        }
    )
    (lib.originals_dir(case.id) / f"{sha[:16]}.jpg").write_bytes(DATA)
    lib.update_case(case.id, lambda c: c.model_copy(update={"photos": [rec]}))
    return lib.case_dir(case.id)


def export(tmp_path: Path) -> tuple[Path, Path]:
    case_dir = make_case_folder(tmp_path / "src")
    zip_path = tmp_path / "out.zip"
    result = CliRunner().invoke(app, ["export-case", str(case_dir), str(zip_path)])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == f"Wrote {zip_path}"
    return case_dir, zip_path


def use_library(root: Path) -> None:
    SettingsStore(app_dirs().settings_file).put(Settings(library_root=root))


def test_export_and_import_case_round_trip(tmp_path: Path) -> None:
    _, zip_path = export(tmp_path)
    library = tmp_path / "lib"
    use_library(library)

    result = CliRunner().invoke(app, ["import-case", str(zip_path)])

    assert result.exit_code == 0, result.output
    assert result.output.strip() == 'Imported "implant 16"'
    folder = library / "Alex Wang" / "20261016 implant 16"
    assert (folder / "case.json").is_file()
    assert len(list((folder / "originals").iterdir())) == 1


def test_export_case_never_overwrites(tmp_path: Path) -> None:
    case_dir, zip_path = export(tmp_path)
    before = zip_path.read_bytes()

    result = CliRunner().invoke(app, ["export-case", str(case_dir), str(zip_path)])

    assert result.exit_code == 1
    assert f"error: {zip_path} already exists." in result.output
    assert zip_path.read_bytes() == before

    result = CliRunner().invoke(app, ["export-case", str(tmp_path), str(tmp_path / "n.zip")])
    assert result.exit_code == 1
    assert f"error: {tmp_path} is not a case folder." in result.output


def test_import_conflict_names_the_options(tmp_path: Path) -> None:
    _, zip_path = export(tmp_path)
    use_library(tmp_path / "lib")
    runner = CliRunner()
    assert runner.invoke(app, ["import-case", str(zip_path)]).exit_code == 0

    again = runner.invoke(app, ["import-case", str(zip_path)])
    assert again.exit_code == 1
    assert "--on-conflict skip" in again.output
    assert "implant 16" in again.output

    skipped = runner.invoke(app, ["import-case", str(zip_path), "--on-conflict", "skip"])
    assert skipped.exit_code == 0
    assert 'Skipped "implant 16": it is already in the library.' in skipped.output

    copied = runner.invoke(app, ["import-case", str(zip_path), "--on-conflict", "copy"])
    assert copied.exit_code == 0
    assert 'Imported "implant 16"' in copied.output


def test_import_refuses_while_the_app_runs(tmp_path: Path) -> None:
    _, zip_path = export(tmp_path)
    use_library(tmp_path / "lib")
    lock = InstanceLock(app_dirs().instance_lock)
    assert lock.try_acquire()
    try:
        result = CliRunner().invoke(app, ["import-case", str(zip_path)])
    finally:
        lock.release()

    assert result.exit_code == 1
    assert "quit it first" in result.output
    assert not (tmp_path / "lib" / "Alex Wang").exists()
