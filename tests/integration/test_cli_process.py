import io
import os
import unicodedata
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pytest
from PIL import ExifTags, Image
from typer.testing import CliRunner

from dental_image_organizer.cli import app
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
