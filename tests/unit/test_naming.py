import unicodedata
from datetime import date, datetime, timedelta, timezone

import pytest

from dental_image_organizer.core import naming
from dental_image_organizer.errors import InvalidNameError


def test_photo_filename_matches_requirements_example() -> None:
    name = naming.photo_filename(
        "AlexWang", "implant maxillary right first molar", datetime(2026, 10, 16, 14, 30, 59)
    )

    assert name == "AlexWang_implant maxillary right first molar_20261016_1430.jpg"


def test_sequence_and_composite_filenames_match_requirements_examples() -> None:
    assert (
        naming.sequence_filename(
            "AlexWang", "implant maxillary right first molar", date(2026, 10, 16)
        )
        == "AlexWang_implant maxillary right first molar_Procedure_20261016.jpg"
    )
    assert (
        naming.composite_filename(
            "AlexWang",
            "Comprehensive oral evaluation and full mouth records",
            date(2026, 10, 16),
        )
        == "AlexWang_Comprehensive oral evaluation and full mouth records_Composite_20261016.jpg"
    )


def test_patient_export_zip_name() -> None:
    assert naming.patient_export_zip_name("Alex Wang", date(2026, 10, 16)) == (
        "Alex Wang_Export_20261016.zip"
    )


def test_long_multibyte_patient_zip_name_keeps_the_export_suffix() -> None:
    name = naming.patient_export_zip_name("王" * 80, date(2026, 10, 16))

    assert len(name.encode()) <= naming.MAX_FILENAME_BYTES
    assert name.endswith("_Export_20261016.zip")
    assert name.startswith("王")


def test_capture_time_uses_the_recorded_wall_clock_not_the_machine_timezone() -> None:
    taipei = timezone(timedelta(hours=8))

    name = naming.photo_filename("A", "B", datetime(2026, 10, 16, 14, 30, tzinfo=taipei))

    assert name.endswith("_20261016_1430.jpg")


def test_nfd_and_nfc_names_produce_identical_file_names() -> None:
    nfd = unicodedata.normalize("NFD", "王小明 Café")
    nfc = unicodedata.normalize("NFC", "王小明 Café")

    assert naming.sanitize_component(nfd) == naming.sanitize_component(nfc) == nfc


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  Alex   Wang ", "Alex Wang"),
        ('a/b\\c:d*e?f"g<h>i|j', "abcdefghij"),
        ("line\nbreak\ttab", "line break tab"),
        ("x / y", "x y"),
    ],
)
def test_sanitize_component(raw: str, expected: str) -> None:
    assert naming.sanitize_component(raw) == expected


def test_component_is_truncated_to_80_characters() -> None:
    assert len(naming.sanitize_component("x" * 200)) == naming.MAX_COMPONENT_CHARS


def test_empty_after_sanitizing_is_rejected() -> None:
    with pytest.raises(InvalidNameError):
        naming.sanitize_component(' /:*?"<> ')


def test_long_multibyte_names_fit_200_bytes_and_keep_the_patient() -> None:
    patient = "王" * 80  # 240 bytes in UTF-8 on its own
    title = "植" * 80

    name = naming.photo_filename(patient, title, datetime(2026, 10, 16, 9, 5))

    assert len(name.encode()) <= naming.MAX_FILENAME_BYTES
    assert name.endswith("_20261016_0905.jpg")
    assert name.startswith("王")


def test_unique_name_adds_numeric_suffixes() -> None:
    taken = {"a_b_20261016_1430.jpg", "A_B_20261016_1430_2.JPG"}

    assert naming.unique_name("a_b_20261016_1430.jpg", taken) == "a_b_20261016_1430_3.jpg"
    assert naming.unique_name("free.jpg", taken) == "free.jpg"


def test_unique_name_treats_nfd_and_nfc_as_the_same_file() -> None:
    taken = {unicodedata.normalize("NFD", "王小明_é.jpg")}

    assert naming.unique_name(unicodedata.normalize("NFC", "王小明_é.jpg"), taken) == (
        unicodedata.normalize("NFC", "王小明_é_2.jpg")
    )


def test_case_zip_names() -> None:
    assert (
        naming.case_export_zip_name("Alex Wang", "implant 16", date(2026, 10, 16))
        == "Alex Wang_implant 16_Export_20261016.zip"
    )
    assert (
        naming.case_archive_name("Alex Wang", "implant 16", date(2026, 10, 16))
        == "Alex Wang_implant 16_Archive_20261016.zip"
    )


def test_case_zip_names_trim_the_title_and_keep_the_patient() -> None:
    for build in (naming.case_export_zip_name, naming.case_archive_name):
        name = build("Alex Wang", "t" * 300, date(2026, 10, 16))
        assert len(name.encode()) <= naming.MAX_FILENAME_BYTES
        assert name.startswith("Alex Wang_t")
        assert name.endswith("_20261016.zip")
