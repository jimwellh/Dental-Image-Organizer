import io
import os
import unicodedata
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import structlog
from PIL import ExifTags, Image
from structlog.testing import capture_logs
from typer.testing import CliRunner

from dental_image_organizer.cli import app
from dental_image_organizer.services import batch_io, headless
from dental_image_organizer.services.headless import run_procedure
from support.scenarios import about_center, make_pair
from support.synthetic import make_scene

W, H = 2400, 1600
TODAY = date.today().strftime("%Y%m%d")


def _jpeg(rgb: np.ndarray, taken: str | None) -> bytes:
    exif = Image.Exif()
    if taken:
        sub = exif.get_ifd(ExifTags.IFD.Exif)
        sub[ExifTags.Base.DateTimeOriginal] = taken
        sub[ExifTags.Base.OffsetTimeOriginal] = "+08:00"
    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, format="JPEG", quality=92, exif=exif.tobytes())
    return buf.getvalue()


@pytest.fixture(scope="module")
def case_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    folder = tmp_path_factory.mktemp("case")
    scene = make_scene(61, W, H)
    first = make_pair(scene, about_center(W, H, 1.0, 4.0))
    (folder / "reference.jpg").write_bytes(_jpeg(first.reference, "2026:10:16 09:00:00"))
    (folder / "IMG_0001.jpg").write_bytes(_jpeg(first.photo, "2026:10:16 14:30:05"))
    burst = make_pair(scene, about_center(W, H, 0.9, -5.0))
    (folder / "IMG_0002.jpg").write_bytes(_jpeg(burst.photo, "2026:10:16 14:30:40"))
    later = make_pair(scene, about_center(W, H, 1.1, 2.0))
    no_exif = folder / "IMG_0003.jpg"
    no_exif.write_bytes(_jpeg(later.photo, None))
    stamp = datetime(2026, 10, 20, 8, 15).timestamp()
    os.utime(no_exif, (stamp, stamp))
    (folder / "IMG_0004.MOV").write_bytes(b"\x00\x00\x00\x14ftypqt  \x00\x00\x00\x00qt  ")
    (folder / "IMG_0001.AAE").write_bytes(b'<?xml version="1.0" encoding="UTF-8"?><plist/>')
    return folder


def _invoke(case_dir: Path, out: Path, *extra: str, patient: str = "Alex Wang") -> object:
    photos = sorted(str(p) for p in case_dir.iterdir() if p.name != "reference.jpg")
    return CliRunner().invoke(
        app,
        [
            "process",
            *photos,
            "--reference",
            str(case_dir / "reference.jpg"),
            "--out",
            str(out),
            "--patient",
            patient,
            "--title",
            "implant 16",
            *extra,
        ],
    )


def test_process_writes_named_outputs_and_a_sequence(case_dir: Path, tmp_path: Path) -> None:
    result = _invoke(case_dir, tmp_path / "out")

    assert result.exit_code == 0, result.output
    names = sorted(p.name for p in (tmp_path / "out").iterdir())
    assert names == sorted(
        [
            "Alex Wang_implant 16_20261016_0900.jpg",
            "Alex Wang_implant 16_20261016_1430.jpg",
            "Alex Wang_implant 16_20261016_1430_2.jpg",
            "Alex Wang_implant 16_20261020_0815.jpg",
            f"Alex Wang_implant 16_Procedure_{TODAY}.jpg",
        ]
    )
    sequence = Image.open(tmp_path / "out" / f"Alex Wang_implant 16_Procedure_{TODAY}.jpg")
    assert sequence.size == (4 * 1200, 800)
    assert "Skipped 2 files: 1 edit sidecar, 1 video" in result.output
    assert "file modification time" in result.output


def test_output_scale_enlarges_every_output(case_dir: Path, tmp_path: Path) -> None:
    result = _invoke(case_dir, tmp_path / "out", "--output-scale", "1.5")

    assert result.exit_code == 0, result.output
    single = Image.open(tmp_path / "out" / "Alex Wang_implant 16_20261016_1430.jpg")
    assert single.size == (1800, 1200)


def test_sequence_respects_the_pixel_cap(
    case_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(headless, "MAX_SEQUENCE_PIXELS", 2_000_000)

    result = _invoke(case_dir, tmp_path / "out")

    assert result.exit_code == 0, result.output
    sequence = Image.open(tmp_path / "out" / f"Alex Wang_implant 16_Procedure_{TODAY}.jpg")
    assert sequence.width * sequence.height <= 2_000_000
    assert sequence.height < 800
    single = Image.open(tmp_path / "out" / "Alex Wang_implant 16_20261016_1430.jpg")
    assert single.size == (1200, 800)


def test_existing_files_are_never_overwritten(case_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (out / "Alex Wang_implant 16_20261016_0900.jpg").write_bytes(b"keep me")

    result = _invoke(case_dir, out)

    assert result.exit_code == 0, result.output
    assert (out / "Alex Wang_implant 16_20261016_0900.jpg").read_bytes() == b"keep me"
    assert (out / "Alex Wang_implant 16_20261016_0900_2.jpg").exists()


def test_nfd_patient_name_produces_nfc_file_names(case_dir: Path, tmp_path: Path) -> None:
    result = _invoke(case_dir, tmp_path / "out", patient=unicodedata.normalize("NFD", "王小明"))

    assert result.exit_code == 0, result.output
    assert (tmp_path / "out" / "王小明_implant 16_20261016_0900.jpg").exists()


def test_corrupt_photo_fails_without_stopping_the_batch(case_dir: Path, tmp_path: Path) -> None:
    broken = tmp_path / "broken.jpg"
    broken.write_bytes((case_dir / "IMG_0001.jpg").read_bytes()[:300])

    result = CliRunner().invoke(
        app,
        [
            "process",
            str(broken),
            str(case_dir / "IMG_0001.jpg"),
            "--reference",
            str(case_dir / "reference.jpg"),
            "--out",
            str(tmp_path / "out"),
            "--patient",
            "A",
            "--title",
            "B",
        ],
    )

    assert result.exit_code == 1
    assert "failed" in result.output
    assert (tmp_path / "out" / "A_B_20261016_1430.jpg").exists()


def test_unreadable_reference_is_an_error(case_dir: Path, tmp_path: Path) -> None:
    bad = tmp_path / "reference.mov"
    bad.write_bytes(b"\x00\x00\x00\x14ftypqt  \x00\x00\x00\x00qt  ")

    result = CliRunner().invoke(
        app,
        [
            "process",
            str(case_dir / "IMG_0001.jpg"),
            "--reference",
            str(bad),
            "--out",
            str(tmp_path / "out"),
            "--patient",
            "A",
            "--title",
            "B",
        ],
    )

    assert result.exit_code == 1
    assert "Videos are not supported" in result.output
    assert not (tmp_path / "out").exists()


def test_invalid_output_scale_is_a_usage_error(case_dir: Path, tmp_path: Path) -> None:
    result = _invoke(case_dir, tmp_path / "out", "--output-scale", "3")

    assert result.exit_code == 2


def test_empty_patient_name_is_rejected_before_writing(case_dir: Path, tmp_path: Path) -> None:
    result = _invoke(case_dir, tmp_path / "out", patient="///")

    assert result.exit_code == 1
    assert not (tmp_path / "out").exists()


def test_unwritable_output_folder_is_an_error_not_a_traceback(
    case_dir: Path, tmp_path: Path
) -> None:
    # A regular file where the parent folder should be makes mkdir fail on every OS.
    blocker = tmp_path / "file.txt"
    blocker.write_bytes(b"not a folder")

    result = _invoke(case_dir, blocker / "out")

    assert result.exit_code == 1
    assert "error:" in result.output


def test_unexpected_error_in_one_photo_fails_only_that_photo(
    case_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_render_edit = headless.render_edit
    calls = 0

    def render_edit_failing_once(*args: Any, **kwargs: Any) -> np.ndarray:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise MemoryError("Unable to allocate 1.10 GiB for an array")
        return real_render_edit(*args, **kwargs)

    monkeypatch.setattr(headless, "render_edit", render_edit_failing_once)
    out = tmp_path / "out"

    result = CliRunner().invoke(
        app,
        [
            "process",
            str(case_dir / "IMG_0001.jpg"),
            str(case_dir / "IMG_0002.jpg"),
            "--reference",
            str(case_dir / "reference.jpg"),
            "--out",
            str(out),
            "--patient",
            "A",
            "--title",
            "B",
        ],
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit), result.exception
    assert "IMG_0001.jpg: " in result.output
    assert "MemoryError" in result.output
    # The failed photo leaves no file; the next photo and the sequence are still written.
    assert sorted(p.name for p in out.iterdir()) == [
        "A_B_20261016_0900.jpg",
        "A_B_20261016_1430.jpg",
        f"A_B_Procedure_{TODAY}.jpg",
    ]
    assert Image.open(out / f"A_B_Procedure_{TODAY}.jpg").size == (2 * 1200, 800)


def test_unexpected_decode_error_fails_only_that_photo(
    case_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_decode = batch_io.decode
    bad = (case_dir / "IMG_0001.jpg").read_bytes()

    def decode_failing_on_one_photo(data: bytes) -> Any:
        if data == bad:
            raise MemoryError  # decoding a 48 MP HEIC holds several full-size copies
        return real_decode(data)

    monkeypatch.setattr(batch_io, "decode", decode_failing_on_one_photo)

    report = run_procedure(
        case_dir / "reference.jpg",
        [case_dir / "IMG_0001.jpg", case_dir / "IMG_0002.jpg"],
        tmp_path / "out",
        patient="A",
        title="B",
    )

    assert [p.status for p in report.photos] == ["failed", "ok"]
    assert report.photos[0].message == "Unexpected MemoryError"
    assert report.sequence_output.exists()


def test_os_error_without_a_path_keeps_its_message(
    case_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_encode = headless.encode_jpeg
    calls = 0

    def encode_failing_on_the_first_photo(*args: Any, **kwargs: Any) -> bytes:
        nonlocal calls
        calls += 1
        if calls == 2:  # call 1 encodes the reference
            raise OSError("broken data stream when writing image file")
        return real_encode(*args, **kwargs)

    monkeypatch.setattr(headless, "encode_jpeg", encode_failing_on_the_first_photo)

    report = run_procedure(
        case_dir / "reference.jpg",
        [case_dir / "IMG_0001.jpg"],
        tmp_path / "out",
        patient="A",
        title="B",
    )

    assert [p.status for p in report.photos] == ["failed"]
    assert report.photos[0].message == "broken data stream when writing image file"


def test_output_beyond_the_jpeg_limit_is_an_error_before_writing(tmp_path: Path) -> None:
    wide = tmp_path / "wide.png"
    Image.fromarray(np.full((4, 32751, 3), 128, np.uint8)).save(wide)

    result = CliRunner().invoke(
        app,
        [
            "process",
            str(wide),
            "--reference",
            str(wide),
            "--out",
            str(tmp_path / "out"),
            "--patient",
            "A",
            "--title",
            "B",
            "--output-scale",
            "2",
        ],
    )

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit), result.exception
    assert "65500 px per side" in result.output
    assert not (tmp_path / "out").exists()


@pytest.mark.usefixtures("debug_logging")
def test_debug_events_carry_the_photo_position(case_dir: Path, tmp_path: Path) -> None:
    # capture_logs() swaps the processor chain, so contextvars are merged explicitly here.
    with capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs:
        run_procedure(
            case_dir / "reference.jpg",
            [case_dir / "IMG_0001.jpg", case_dir / "IMG_0002.jpg"],
            tmp_path / "out",
            patient="A",
            title="B",
        )

    events = [entry for entry in logs if entry["event"] == "align"]
    assert [entry.get("photo") for entry in events] == [1, 2]


@pytest.mark.usefixtures("debug_logging")
def test_logs_and_messages_never_name_the_patient(case_dir: Path, tmp_path: Path) -> None:
    # Patient folders and re-processed exports put the patient's name into every path.
    folder = tmp_path / "Alex Wang"
    folder.mkdir()
    reference = folder / "Alex Wang pre-op.jpg"
    Image.open(case_dir / "reference.jpg").save(reference, quality=92)  # drops the EXIF time
    photo = folder / "Alex Wang_implant 16_20261016_1430.jpg"
    photo.write_bytes((case_dir / "IMG_0001.jpg").read_bytes())
    # A directory with a photo's name makes read_bytes() raise an OSError on every OS.
    unreadable = folder / "Alex Wang_implant 16_20261016_1431.jpg"
    unreadable.mkdir()

    with capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs:
        report = run_procedure(
            reference, [photo, unreadable], tmp_path / "out", patient="Alex Wang", title="B"
        )

    by_event = {entry["event"]: entry for entry in logs}
    assert by_event["capture_time_from_file"]["photo"] == 0
    assert by_event["align"]["photo"] == 1
    assert by_event["photo_unreadable"]["photo"] == 2
    assert [p.status for p in report.photos] == ["ok", "failed"]
    assert all("Alex Wang" not in repr(entry) for entry in logs)
    assert "Alex Wang" not in str(report.photos[1].message)
