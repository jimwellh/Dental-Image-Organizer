"""The library folder: patients and cases as plain folders (spec §4.11)."""

import json
import os
import shutil
import threading
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from dental_image_organizer.errors import (
    CaseNotFoundError,
    InvalidNameError,
    PathOutsideLibraryError,
    StorageError,
)
from dental_image_organizer.storage.library import CaseLocks, Library
from dental_image_organizer.storage.schema import PhotoRecord, new_id

NOW = datetime(2026, 10, 16, 14, 30, tzinfo=timezone(timedelta(hours=8)))


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def lib(tmp_path: Path, clock: Clock) -> Library:
    return Library(tmp_path / "library", CaseLocks(), clock=clock)


def make_photo(role: str = "photo") -> PhotoRecord:
    return PhotoRecord.model_validate(
        {
            "id": new_id(),
            "sha256": "a" * 64,
            "file": "originals/0123456789abcdef.jpg",
            "source_name": "x.jpg",
            "role": role,
        }
    )


def test_create_patient_and_case(lib: Library) -> None:
    patient = lib.create_patient("Alex Wang", "chart 12")
    case = lib.create_case(patient.id, "procedure", "implant 16")

    stored = json.loads((lib.root / "Alex Wang" / "patient.json").read_text("utf-8"))
    assert stored["note"] == "chart 12"
    case_json = lib.root / "Alex Wang" / "20261016 implant 16" / "case.json"
    assert case_json.is_file()
    assert case.output_scale == 1.0
    assert case.sequence is not None
    assert case.sequence.photo_ids == []
    listing = lib.listing()
    assert [e.record.id for e in listing.patients] == [patient.id]
    assert [c.id for c in listing.patients[0].cases] == [case.id]
    assert listing.problems == ()


@pytest.mark.parametrize(
    ("names", "expected"),
    [
        (["王小明 Café"], ["王小明 Café"]),
        (["Alex/Wang"], ["AlexWang"]),
        (["a:b"], ["ab"]),
        ([".trash"], ["trash"]),
        (["Alex Wang", "Alex Wang"], ["Alex Wang", "Alex Wang (2)"]),
        (["Alex Wang", "alex wang"], ["Alex Wang", "alex wang (2)"]),
    ],
)
def test_folder_names_are_safe_and_unique(
    lib: Library, names: list[str], expected: list[str]
) -> None:
    for name in names:
        lib.create_patient(name, None)
    folders = sorted(p.name for p in lib.root.iterdir())
    assert folders == sorted(unicodedata.normalize("NFC", e) for e in expected)


def test_a_name_of_only_dots_is_rejected(lib: Library) -> None:
    with pytest.raises(InvalidNameError):
        lib.create_patient("..", None)


def test_update_case_refreshes_the_timestamp(lib: Library, clock: Clock) -> None:
    patient = lib.create_patient("Alex Wang", None)
    case = lib.create_case(patient.id, "procedure", "implant 16")
    clock.now = NOW + timedelta(hours=1)

    updated = lib.update_case(case.id, lambda c: c.model_copy(update={"title": "new"}))

    assert updated.updated_at == clock.now
    stored = lib.read_case(case.id)
    assert stored.title == "new"
    assert stored.updated_at == clock.now


def test_an_invalid_change_leaves_the_file_unchanged(lib: Library) -> None:
    patient = lib.create_patient("Alex Wang", None)
    case = lib.create_case(patient.id, "procedure", "implant 16")
    path = lib.case_dir(case.id) / "case.json"
    before = path.read_bytes()

    def two_references(c):  # type: ignore[no-untyped-def]
        return c.model_copy(update={"photos": [make_photo("reference"), make_photo("reference")]})

    with pytest.raises(ValidationError):
        lib.update_case(case.id, two_references)
    assert path.read_bytes() == before


def test_concurrent_updates_lose_nothing(lib: Library) -> None:
    patient = lib.create_patient("Alex Wang", None)
    case = lib.create_case(patient.id, "procedure", "implant 16")
    errors: list[BaseException] = []

    def add() -> None:
        try:
            photo = make_photo()
            lib.update_case(case.id, lambda c: c.model_copy(update={"photos": [*c.photos, photo]}))
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=add) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert len(lib.read_case(case.id).photos) == 8


def test_rename_case_and_patient_move_their_folders(lib: Library) -> None:
    patient = lib.create_patient("Alex Wang", None)
    case = lib.create_case(patient.id, "procedure", "implant 16")

    renamed = lib.rename_case(case.id, "implant 26")
    assert renamed.title == "implant 26"
    assert lib.case_dir(case.id) == lib.root / "Alex Wang" / "20261016 implant 26"
    assert lib.read_case(case.id).title == "implant 26"

    lib.update_patient(patient.id, name="Alex Wong", note=None)
    assert (lib.root / "Alex Wong" / "patient.json").is_file()
    assert not (lib.root / "Alex Wang").exists()
    assert lib.case_dir(case.id) == lib.root / "Alex Wong" / "20261016 implant 26"
    assert lib.read_case(case.id).title == "implant 26"


def test_trash_case_keeps_it_restorable(lib: Library) -> None:
    patient = lib.create_patient("Alex Wang", None)
    case = lib.create_case(patient.id, "procedure", "implant 16")

    target = lib.trash_case(case.id)

    assert target == lib.root / ".trash" / "Alex Wang" / "20261016 implant 16"
    assert (target / "case.json").is_file()
    assert (lib.root / ".trash" / "Alex Wang" / "patient.json").is_file()
    with pytest.raises(CaseNotFoundError):
        lib.read_case(case.id)
    assert lib.listing().patients[0].cases == ()


def test_folders_changed_in_finder_are_reported_not_fatal(lib: Library) -> None:
    patient = lib.create_patient("Alex Wang", None)
    first = lib.create_case(patient.id, "procedure", "implant 16")
    second = lib.create_case(patient.id, "procedure", "implant 26")
    pdir = lib.root / "Alex Wang"

    damaged = pdir / "20261016 damaged"
    damaged.mkdir()
    (damaged / "case.json").write_bytes(b"{")
    newer = pdir / "20261016 newer"
    newer.mkdir()
    (newer / "case.json").write_text(json.dumps({"schema_version": 2}), "utf-8")
    shutil.copytree(lib.case_dir(first.id), pdir / "20261016 implant 16 copy")
    (lib.root / "Downloads stuff").mkdir()

    listing = lib.listing()

    ids = {c.id for c in listing.patients[0].cases}
    assert ids == {first.id, second.id}
    by_folder = {p.folder: p.message for p in listing.problems}
    assert set(by_folder) == {
        "Alex Wang/20261016 damaged",
        "Alex Wang/20261016 newer",
        "Alex Wang/20261016 implant 16 copy",
    }
    assert "damaged" in by_folder["Alex Wang/20261016 damaged"]
    assert "newer version" in by_folder["Alex Wang/20261016 newer"]
    assert "20261016 implant 16" in by_folder["Alex Wang/20261016 implant 16 copy"]
    assert "duplicates" in by_folder["Alex Wang/20261016 implant 16 copy"]
    assert all("Downloads stuff" not in p.folder for p in listing.problems)


def test_a_case_folder_renamed_in_finder_is_found_again(lib: Library) -> None:
    patient = lib.create_patient("Alex Wang", None)
    case = lib.create_case(patient.id, "procedure", "implant 16")
    lib.read_case(case.id)
    old = lib.case_dir(case.id)

    os.rename(old, old.with_name("20261016 renamed"))

    assert lib.read_case(case.id).id == case.id
    assert lib.case_dir(case.id) == old.with_name("20261016 renamed")


def test_a_case_folder_deleted_in_finder_is_not_found(lib: Library) -> None:
    patient = lib.create_patient("Alex Wang", None)
    case = lib.create_case(patient.id, "procedure", "implant 16")
    lib.read_case(case.id)

    shutil.rmtree(lib.case_dir(case.id))

    with pytest.raises(CaseNotFoundError):
        lib.read_case(case.id)


def test_resolve_confines_paths_to_the_library(lib: Library, tmp_path: Path) -> None:
    lib.root.mkdir()
    assert lib.resolve("a/b.jpg") == (lib.root / "a" / "b.jpg").resolve()
    with pytest.raises(PathOutsideLibraryError):
        lib.resolve("../outside")
    with pytest.raises(PathOutsideLibraryError):
        lib.resolve(tmp_path / "outside")


def test_resolve_rejects_a_symlink_that_leaves_the_library(lib: Library, tmp_path: Path) -> None:
    lib.root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        os.symlink(outside, lib.root / "link", target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are not permitted on this host")
    with pytest.raises(PathOutsideLibraryError):
        lib.resolve("link/photo.jpg")


def test_a_failed_listing_never_reuses_a_taken_name(
    lib: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    lib.create_patient("Alex Wang", None)
    path = lib.root / "Alex Wang" / "patient.json"
    before = path.read_bytes()

    def denied(self: Path) -> None:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "iterdir", denied)
    with pytest.raises(StorageError):
        lib.create_patient("Alex Wang", None)
    assert path.read_bytes() == before


def test_unreadable_folders_are_reported(lib: Library, monkeypatch: pytest.MonkeyPatch) -> None:
    a = lib.create_patient("Anna", None)
    b = lib.create_patient("Ben", None)
    lib.create_case(a.id, "procedure", "x")
    real_scandir = os.scandir

    def scandir(path: str | Path):  # type: ignore[no-untyped-def]
        if Path(path).name == "Ben":
            raise PermissionError(13, "Permission denied")
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", scandir)
    listing = lib.listing()
    assert {e.record.id for e in listing.patients} == {a.id, b.id}
    assert [p.folder for p in listing.problems] == ["Ben"]
    assert "could not be read" in listing.problems[0].message

    def root_denied(path: str | Path):  # type: ignore[no-untyped-def]
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(os, "scandir", root_denied)
    listing = lib.listing()
    assert [p.folder for p in listing.problems] == ["."]


def test_a_symlinked_case_folder_is_not_listed(lib: Library, tmp_path: Path) -> None:
    other = Library(tmp_path / "elsewhere", CaseLocks(), clock=Clock())
    outsider = other.create_patient("Out", None)
    outside_case = other.create_case(outsider.id, "procedure", "t")
    patient = lib.create_patient("Alex Wang", None)
    link = lib.root / "Alex Wang" / "20261016 link"
    try:
        os.symlink(other.case_dir(outside_case.id), link, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are not permitted on this host")
    assert lib.listing().patients[0].cases == ()
    assert patient.id
    with pytest.raises(CaseNotFoundError):
        lib.read_case(outside_case.id)


def test_update_patient_waits_for_case_locks(lib: Library) -> None:
    locks = lib._locks
    patient = lib.create_patient("Alex Wang", None)
    case = lib.create_case(patient.id, "procedure", "implant 16")
    held, release = threading.Event(), threading.Event()

    def holder() -> None:
        with locks.hold(case.id):
            held.set()
            release.wait(5)

    t1 = threading.Thread(target=holder)
    t1.start()
    assert held.wait(5)
    t2 = threading.Thread(
        target=lambda: lib.update_patient(patient.id, name="Alex Wong", note=None)
    )
    t2.start()
    t2.join(0.3)
    assert t2.is_alive()
    assert (lib.root / "Alex Wang").exists()
    release.set()
    t1.join(5)
    t2.join(5)
    assert not t2.is_alive()
    assert (lib.root / "Alex Wong").is_dir()
    assert lib.read_case(case.id).id == case.id
