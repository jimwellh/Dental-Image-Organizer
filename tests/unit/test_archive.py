import hashlib
import threading
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from dental_image_organizer.errors import CaseExistsError, InvalidArchiveError
from dental_image_organizer.storage import archive
from dental_image_organizer.storage.archive import import_case_archive, write_case_archive
from dental_image_organizer.storage.library import CaseLocks, Library
from dental_image_organizer.storage.schema import CaseRecord, PhotoRecord, new_id

NOW = datetime(2026, 10, 16, 14, 30, tzinfo=timezone(timedelta(hours=8)))
BYTES = [b"\xff\xd8first original\xff\xd9", b"\xff\xd8second original\xff\xd9"]


def make_lib(path: Path) -> Library:
    return Library(path, CaseLocks(), clock=lambda: NOW)


def photo(data: bytes, role: str) -> PhotoRecord:
    sha = hashlib.sha256(data).hexdigest()
    return PhotoRecord.model_validate(
        {
            "id": new_id(),
            "sha256": sha,
            "file": f"originals/{sha[:16]}.jpg",
            "source_name": "x.jpg",
            "role": role,
        }
    )


@pytest.fixture
def source(tmp_path: Path) -> Library:
    return make_lib(tmp_path / "source")


@pytest.fixture
def target(tmp_path: Path) -> Library:
    return make_lib(tmp_path / "target")


def build_case(lib: Library) -> CaseRecord:
    patient = lib.create_patient("Alex Wang", "chart 12")
    case = lib.create_case(patient.id, "procedure", "implant 16")
    photos = [photo(BYTES[0], "reference"), photo(BYTES[1], "photo")]
    originals = lib.originals_dir(case.id)
    for rec, data in zip(photos, BYTES, strict=True):
        (originals.parent / rec.file).write_bytes(data)
    return lib.update_case(case.id, lambda c: c.model_copy(update={"photos": photos}))


def members(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as zf:
        return {i.filename: zf.read(i) for i in zf.infolist()}


def rewrite(
    src: Path, dest: Path, change: dict[str, bytes | None], extra: dict[str, bytes]
) -> Path:
    items = members(src)
    for name, data in change.items():
        if data is None:
            del items[name]
        else:
            items[name] = data
    items.update(extra)
    with zipfile.ZipFile(dest, "w") as zf:
        for name, data in items.items():
            zf.writestr(name, data)
    return dest


def test_archive_round_trip(source: Library, target: Library, tmp_path: Path) -> None:
    case = build_case(source)
    zip_path = tmp_path / "case.zip"
    write_case_archive(source.case_dir(case.id), zip_path)

    outcome = import_case_archive(target, zip_path)

    assert outcome.status == "imported"
    assert (outcome.case_id, outcome.patient_id) == (case.id, case.patient_id)
    imported = target.read_case(case.id)
    skip = {"exports", "updated_at"}
    assert imported.model_dump(exclude=skip) == case.model_dump(exclude=skip)
    assert imported.exports == []
    for rec, data in zip(case.photos, BYTES, strict=True):
        assert (target.case_dir(case.id) / rec.file).read_bytes() == data
    entry = target.patient(case.patient_id)
    assert (entry.record.name, entry.record.note) == ("Alex Wang", "chart 12")
    assert entry.record.created_at == source.patient(case.patient_id).record.created_at
    assert not list(target.root.glob(".importing-*"))


def test_archive_holds_only_referenced_originals(source: Library, tmp_path: Path) -> None:
    case = build_case(source)
    folder = source.case_dir(case.id)
    (folder / "originals" / "ffffffffffffffff.jpg").write_bytes(b"stray")
    zip_path = tmp_path / "case.zip"

    write_case_archive(folder, zip_path)

    assert set(members(zip_path)) == {"patient.json", "case.json", *(p.file for p in case.photos)}
    with zipfile.ZipFile(zip_path) as zf:
        kinds = {i.filename: i.compress_type for i in zf.infolist()}
    assert kinds["case.json"] == zipfile.ZIP_DEFLATED
    assert kinds[case.photos[0].file] == zipfile.ZIP_STORED


def test_conflict_policies(source: Library, target: Library, tmp_path: Path) -> None:
    case = build_case(source)
    zip_path = tmp_path / "case.zip"
    write_case_archive(source.case_dir(case.id), zip_path)
    import_case_archive(target, zip_path)

    with pytest.raises(CaseExistsError) as info:
        import_case_archive(target, zip_path)
    assert info.value.details == {"case_id": case.id, "title": "implant 16"}

    skipped = import_case_archive(target, zip_path, on_conflict="skip")
    assert skipped.status == "skipped"
    assert len(target.patient(case.patient_id).cases) == 1

    copied = import_case_archive(target, zip_path, on_conflict="copy")
    assert copied.status == "imported"
    assert copied.case_id != case.id
    assert len(target.patient(case.patient_id).cases) == 2
    assert target.case_dir(copied.case_id).name == "20261016 implant 16 (2)"
    assert [p.id for p in target.read_case(copied.case_id).photos] == [p.id for p in case.photos]
    assert len(target.listing().patients) == 1


def _write(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path


def utf8_flagged_member(src: Path, dest: Path) -> Path:
    """A member whose name is flagged as UTF-8 but is not valid UTF-8."""
    stub = dest.with_name("stub.zip")
    rewrite(src, stub, {}, {"\u00e9\u00e9": b"x"})
    raw = stub.read_bytes()
    assert raw.count("\u00e9\u00e9".encode()) == 2  # local header and central directory
    dest.write_bytes(raw.replace("\u00e9\u00e9".encode(), b"\xff\xfe\xff\xfe"))
    return dest


def bad_archive(name: str, good: Path, tmp_path: Path, case: CaseRecord) -> Path:
    case_json = members(good)["case.json"]
    ref = case.photos[0].file
    dest = tmp_path / "bad.zip"
    match name:
        case "not a zip":
            return _write(dest, b"this is not a zip")
        case "path traversal":
            return rewrite(good, dest, {}, {"../evil.jpg": b"x"})
        case "extra member":
            return rewrite(good, dest, {}, {"notes.txt": b"x"})
        case "changed original":
            return rewrite(good, dest, {ref: b"tampered"}, {})
        case "missing original":
            return rewrite(good, dest, {ref: None}, {})
        case "other patient":
            changed = case_json.replace(case.patient_id.encode(), new_id().encode())
            return rewrite(good, dest, {"case.json": changed}, {})
        case "blank title":
            changed = case_json.replace(b'"title": "implant 16"', b'"title": " . "')
            return rewrite(good, dest, {"case.json": changed}, {})
        case "dot patient name":
            changed = members(good)["patient.json"].replace(b'"name": "Alex Wang"', b'"name": "."')
            return rewrite(good, dest, {"patient.json": changed}, {})
        case "invalid utf-8 name":
            return utf8_flagged_member(good, dest)
        case "newer schema":
            changed = case_json.replace(b'"schema_version": 1', b'"schema_version": 2')
            return rewrite(good, dest, {"case.json": changed}, {})
    raise AssertionError(name)


@pytest.mark.parametrize(
    "name",
    [
        "not a zip",
        "path traversal",
        "extra member",
        "changed original",
        "missing original",
        "other patient",
        "newer schema",
        "blank title",
        "dot patient name",
        "invalid utf-8 name",
    ],
)
def test_invalid_archives_change_nothing(
    name: str, source: Library, target: Library, tmp_path: Path
) -> None:
    case = build_case(source)
    good = tmp_path / "good.zip"
    write_case_archive(source.case_dir(case.id), good)
    bad = bad_archive(name, good, tmp_path, case)

    with pytest.raises(InvalidArchiveError) as info:
        import_case_archive(target, bad)

    if name == "newer schema":
        assert "newer version" in str(info.value)
    if name in {"changed original", "missing original"}:
        assert str(info.value) == "The archive is damaged: a photo does not match its record."
    if name == "extra member":
        assert "notes.txt" in str(info.value)
    assert target.listing().patients == ()
    assert not list(target.root.glob(".importing-*"))


def test_member_sizes_are_capped_by_header(
    source: Library, target: Library, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = build_case(source)
    good = tmp_path / "good.zip"
    write_case_archive(source.case_dir(case.id), good)
    monkeypatch.setattr(archive, "MAX_ORIGINAL_BYTES", len(BYTES[0]) - 1)

    with pytest.raises(InvalidArchiveError, match="too large"):
        import_case_archive(target, good)
    assert target.listing().patients == ()


def test_copy_capped_stops_at_the_limit_whatever_the_header_says(
    source: Library, tmp_path: Path
) -> None:
    case = build_case(source)
    good = tmp_path / "good.zip"
    write_case_archive(source.case_dir(case.id), good)
    dest = tmp_path / "out.bin"

    with zipfile.ZipFile(good) as zf:
        info = zf.getinfo(case.photos[0].file)
        assert info.file_size > 5
        with pytest.raises(InvalidArchiveError, match="too large"):
            archive._copy_capped(zf, info, dest, 5)


def test_a_forged_header_leaves_no_trace(
    source: Library, target: Library, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = build_case(source)
    good = tmp_path / "good.zip"
    write_case_archive(source.case_dir(case.id), good)
    ref = case.photos[0].file
    # Both honest originals fit; only the forged member is larger than its header claims.
    monkeypatch.setattr(archive, "MAX_ORIGINAL_BYTES", max(map(len, BYTES)))
    forged = rewrite(good, tmp_path / "forged.zip", {ref: BYTES[0] + b"x" * 5000}, {})
    real_infolist = zipfile.ZipFile.infolist

    def understated(self: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
        infos = real_infolist(self)
        for i in infos:
            if i.filename == ref:
                i.file_size = 1
        return infos

    monkeypatch.setattr(zipfile.ZipFile, "infolist", understated)
    with pytest.raises(InvalidArchiveError):
        import_case_archive(target, forged)
    assert target.listing().patients == ()
    assert not list(target.root.glob(".importing-*"))


def test_adopt_and_install_refuse_ids_that_are_already_taken(
    source: Library, target: Library, tmp_path: Path
) -> None:
    case = build_case(source)
    zip_path = tmp_path / "case.zip"
    write_case_archive(source.case_dir(case.id), zip_path)
    import_case_archive(target, zip_path)
    patient = source.patient(case.patient_id).record

    assert target.adopt_patient(patient).folder.name == "Alex Wang"
    assert [p.name for p in target.root.iterdir()] == ["Alex Wang"]
    staged = target.staging_dir()
    with pytest.raises(CaseExistsError) as info:
        target.install_case(staged, case)
    assert info.value.details == {"case_id": case.id, "title": "implant 16"}
    assert staged.exists()  # the caller owns the staging folder


def test_remove_empty_patient_only_removes_an_empty_patient(
    source: Library, target: Library, tmp_path: Path
) -> None:
    case = build_case(source)
    empty = target.adopt_patient(source.patient(case.patient_id).record)
    target.remove_empty_patient(empty.record.id)
    assert list(target.root.iterdir()) == []

    full = target.create_patient("Sam", None)
    target.create_case(full.id, "procedure", "x")
    target.remove_empty_patient(full.id)
    assert (target.root / "Sam" / "patient.json").is_file()


@pytest.mark.parametrize("policy", ["ask", "skip"])
def test_a_case_installed_meanwhile_is_a_conflict_too(
    policy: str,
    source: Library,
    target: Library,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = build_case(source)
    zip_path = tmp_path / "case.zip"
    write_case_archive(source.case_dir(case.id), zip_path)
    # Another import lands between this one's check and its install.
    monkeypatch.setattr(archive, "_find_case", lambda lib, case_id: None)
    other = make_lib(tmp_path / "other")
    import_case_archive(other, zip_path)
    first = import_case_archive(target, zip_path)
    assert first.status == "imported"

    if policy == "ask":
        with pytest.raises(CaseExistsError):
            import_case_archive(target, zip_path)
    else:
        assert import_case_archive(target, zip_path, on_conflict="skip").status == "skipped"
    assert len(target.listing().patients) == 1
    assert len(target.patient(case.patient_id).cases) == 1
    assert not list(target.root.glob(".importing-*"))


def test_concurrent_imports_of_one_archive_keep_one_patient_and_case(
    source: Library, target: Library, tmp_path: Path
) -> None:
    case = build_case(source)
    zip_path = tmp_path / "case.zip"
    write_case_archive(source.case_dir(case.id), zip_path)
    barrier = threading.Barrier(2)
    outcomes: list[str] = []

    def run() -> None:
        barrier.wait()
        outcomes.append(import_case_archive(target, zip_path, on_conflict="skip").status)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(outcomes) == ["imported", "skipped"]
    listing = target.listing()
    assert listing.problems == ()
    assert len(listing.patients) == 1
    assert len(listing.patients[0].cases) == 1
    assert not list(target.root.glob(".importing-*"))


def test_clean_staging_removes_leftovers(target: Library) -> None:
    leftover = target.root / ".importing-x"
    (leftover / "originals").mkdir(parents=True)
    (leftover / "originals" / "a.jpg").write_bytes(b"x")
    fresh = target.staging_dir()
    assert fresh.parent == target.root
    assert fresh.name.startswith(".importing-")

    target.clean_staging()

    assert not leftover.exists()
    assert not fresh.exists()
