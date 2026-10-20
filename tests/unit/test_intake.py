"""Content-addressed photo intake and skip reasons (spec §3.1, §4.11, §4.13)."""

from __future__ import annotations

import hashlib
import unicodedata
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from dental_image_organizer.services import intake
from dental_image_organizer.services.context import AppContext
from dental_image_organizer.services.intake import Skipped, add_photos, display_name
from dental_image_organizer.storage.library import Library
from dental_image_organizer.storage.schema import CaseRecord
from support.app import jpeg, upload

VIDEO = b"\x00\x00\x00\x14ftypqt  \x00\x00\x00\x00qt  "
SIDECAR = b'<?xml version="1.0" encoding="UTF-8"?><plist version="1.0"/>'
RAW = b"II*\x00" + bytes(60)
TEXT = b"Notes from the visit."
DUPLICATE = "This photo is already in the case."
VANISHED = "The photo's file was removed while it was being added; drop it again."


def _case(ctx: AppContext) -> str:
    library = ctx.library()
    patient = library.create_patient("Alex Wang", None)
    return library.create_case(patient.id, "procedure", "implant 16").id


def _photo(shade: int) -> bytes:
    return jpeg(np.full((48, 64, 3), shade, np.uint8))


def _originals(ctx: AppContext, case_id: str) -> list[str]:
    return sorted(p.name for p in (ctx.library().case_dir(case_id) / "originals").iterdir())


def test_photos_are_stored_by_content_and_recorded_pending(ctx: AppContext) -> None:
    case_id = _case(ctx)
    first, second = _photo(40), _photo(200)
    modified = datetime(2026, 10, 16, 9, 30, 15)

    added, skipped = add_photos(
        ctx,
        case_id,
        [upload("IMG_0001.JPG", first, modified), upload("IMG_0002.JPG", second)],
        role="photo",
    )

    case = ctx.library().read_case(case_id)
    folder = ctx.library().case_dir(case_id)
    assert skipped == []
    assert added == [p.id for p in case.photos]
    for photo, data, name in zip(
        case.photos, (first, second), ("IMG_0001.JPG", "IMG_0002.JPG"), strict=True
    ):
        sha = hashlib.sha256(data).hexdigest()
        assert photo.sha256 == sha
        assert photo.file == f"originals/{sha[:16]}.jpg"
        assert (folder / photo.file).read_bytes() == data
        assert photo.source_name == name
        assert (photo.state, photo.role, photo.stage, photo.slot) == (
            "pending",
            "photo",
            None,
            None,
        )
        assert photo.edit is None
        assert photo.captured_at_source == "file_mtime"
    assert case.photos[0].captured_at == modified
    assert case.photos[1].captured_at is not None


def test_unsupported_files_are_skipped_with_their_reason(ctx: AppContext) -> None:
    case_id = _case(ctx)
    video = upload("IMG_0004.MOV", VIDEO + bytes(100_000))

    added, skipped = add_photos(
        ctx,
        case_id,
        [
            video,
            upload("IMG_0001.AAE", SIDECAR),
            upload("IMG_0002.DNG", RAW),
            upload("notes.txt", TEXT),
        ],
        role="photo",
    )

    assert added == []
    assert [(s.name, s.reason) for s in skipped] == [
        ("IMG_0004.MOV", "video"),
        ("IMG_0001.AAE", "sidecar"),
        ("IMG_0002.DNG", "raw"),
        ("notes.txt", "unknown"),
    ]
    assert all(s.message for s in skipped)
    # Only the sniffed head of a video is read.
    assert video.stream.tell() == intake.SNIFF_BYTES
    assert _originals(ctx, case_id) == []
    assert ctx.library().read_case(case_id).photos == []


def test_dropping_the_same_photo_again_is_reported_as_duplicate(ctx: AppContext) -> None:
    case_id = _case(ctx)
    a, b, c = _photo(30), _photo(120), _photo(220)
    add_photos(ctx, case_id, [upload("A.jpg", a), upload("B.jpg", b)], role="photo")

    added, skipped = add_photos(
        ctx,
        case_id,
        [upload("A.jpg", a), upload("C.jpg", c), upload("C copy.jpg", c)],
        role="photo",
    )

    case = ctx.library().read_case(case_id)
    assert skipped == [
        Skipped("A.jpg", "duplicate", DUPLICATE),
        Skipped("C copy.jpg", "duplicate", DUPLICATE),
    ]
    assert added == [case.photos[2].id]
    assert [p.source_name for p in case.photos] == ["A.jpg", "B.jpg", "C.jpg"]
    assert len({p.sha256 for p in case.photos}) == 3
    assert len(_originals(ctx, case_id)) == 3


def test_a_photo_whose_staged_file_vanished_is_recorded_failed(
    ctx: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    case_id = _case(ctx)
    a, b, c = _photo(30), _photo(120), _photo(220)
    sha = hashlib.sha256(b).hexdigest()
    lost = ctx.library().case_dir(case_id) / "originals" / f"{sha[:16]}.jpg"
    real_update_case = Library.update_case

    def deleting_first(
        self: Library, case_id: str, change: Callable[[CaseRecord], CaseRecord]
    ) -> CaseRecord:
        # A concurrent delete of a photo with the same bytes, between staging and the change.
        lost.unlink()
        return real_update_case(self, case_id, change)

    monkeypatch.setattr(Library, "update_case", deleting_first)

    added, skipped = add_photos(
        ctx,
        case_id,
        [upload("A.jpg", a), upload("B.jpg", b), upload("C.jpg", c)],
        role="photo",
    )

    case = ctx.library().read_case(case_id)
    assert skipped == []
    assert added == [p.id for p in case.photos]
    assert [(p.source_name, p.state, p.error) for p in case.photos] == [
        ("A.jpg", "pending", None),
        ("B.jpg", "failed", VANISHED),
        ("C.jpg", "pending", None),
    ]
    vanished = case.photos[1]
    assert (vanished.sha256, vanished.file) == (sha, f"originals/{lost.name}")
    assert vanished.captured_at is not None
    assert vanished.captured_at_source == "file_mtime"


def test_files_over_the_limit_are_skipped(ctx: AppContext, monkeypatch: pytest.MonkeyPatch) -> None:
    case_id = _case(ctx)
    monkeypatch.setattr(intake, "MAX_UPLOAD_BYTES", 1000)
    big = jpeg(np.random.default_rng(1).integers(0, 255, (64, 64, 3), dtype=np.uint8))
    assert len(big) > 1000

    added, skipped = add_photos(ctx, case_id, [upload("big.jpg", big)], role="photo")

    assert added == []
    assert skipped == [Skipped("big.jpg", "too_large", "Files over 200 MB are not accepted.")]
    assert _originals(ctx, case_id) == []


def test_upload_names_never_become_paths(ctx: AppContext, tmp_path: Path) -> None:
    case_id = _case(ctx)

    add_photos(
        ctx,
        case_id,
        [upload("../../evil.jpg", _photo(10)), upload("C:\\x\\y.jpg", _photo(90))],
        role="photo",
    )

    case = ctx.library().read_case(case_id)
    assert [p.source_name for p in case.photos] == ["evil.jpg", "y.jpg"]
    assert all(p.file.startswith("originals/") for p in case.photos)
    assert len(_originals(ctx, case_id)) == 2
    assert list(tmp_path.rglob("evil.jpg")) == list(tmp_path.rglob("y.jpg")) == []
    assert display_name("") == display_name("photos/") == "photo"
    assert display_name("bad\x00\x1fname\udc80.jpg") == "badname.jpg"
    assert display_name(unicodedata.normalize("NFD", "Café.jpg")) == "Café.jpg"
    assert len(display_name("x" * 300 + ".jpg")) == 255
