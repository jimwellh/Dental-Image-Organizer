"""Export file names (spec §4.9)."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Iterable
from datetime import date, datetime

from dental_image_organizer.errors import InvalidNameError

MAX_COMPONENT_CHARS = 80
MAX_FILENAME_BYTES = 200

_WHITESPACE = re.compile(r"\s+")
_FORBIDDEN = re.compile(r'[/\\:*?"<>|\x00-\x1f\x7f]')


def sanitize_component(text: str) -> str:
    # NFC first: macOS returns NFD file names, so one typed name can arrive in either form.
    cleaned = unicodedata.normalize("NFC", text)
    cleaned = _WHITESPACE.sub(" ", cleaned)
    cleaned = _FORBIDDEN.sub("", cleaned)
    cleaned = _WHITESPACE.sub(" ", cleaned).strip()
    cleaned = cleaned[:MAX_COMPONENT_CHARS].rstrip()
    if not cleaned:
        raise InvalidNameError(f"{text!r} is empty after removing characters not allowed in names")
    return cleaned


def photo_filename(patient: str, title: str, captured_at: datetime) -> str:
    return _join(patient, title, f"_{captured_at:%Y%m%d}_{captured_at:%H%M}.jpg")


def sequence_filename(patient: str, title: str, generated_on: date) -> str:
    return _join(patient, title, f"_Procedure_{generated_on:%Y%m%d}.jpg")


def composite_filename(patient: str, tag: str, captured_on: date) -> str:
    return _join(patient, tag, f"_Composite_{captured_on:%Y%m%d}.jpg")


def composite_capture_date(dates: Iterable[date]) -> date:
    """Most frequent capture date; the earliest wins a tie so the name is deterministic."""
    counts = Counter(dates)
    if not counts:
        raise ValueError("A composite needs at least one capture date.")
    return min(counts, key=lambda d: (-counts[d], d))


def patient_export_zip_name(patient: str, generated_on: date) -> str:
    # The suffix is fixed by spec §4.9 and identifies the archive, so only the patient shrinks.
    suffix = f"_Export_{generated_on:%Y%m%d}.zip"
    p = sanitize_component(patient)
    while len(f"{p}{suffix}".encode()) > MAX_FILENAME_BYTES and len(p) > 1:
        p = p[:-1].rstrip() or p[:1]
    return f"{p}{suffix}"


def case_export_zip_name(patient: str, title: str, generated_on: date) -> str:
    return _join(patient, title, f"_Export_{generated_on:%Y%m%d}.zip")


def case_archive_name(patient: str, title: str, created_on: date) -> str:
    return _join(patient, title, f"_Archive_{created_on:%Y%m%d}.zip")


def unique_name(name: str, taken: Iterable[str]) -> str:
    """Return `name`, or `name` with `_2`, `_3`, ... before the extension if it is taken.

    Comparison ignores case and Unicode normalization, because the default macOS (APFS) and
    Windows (NTFS) file systems do too.
    """
    keys = {_key(t) for t in taken}
    if _key(name) not in keys:
        return name
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    n = 2
    while True:
        candidate = f"{stem}_{n}.{ext}" if ext else f"{stem}_{n}"
        if _key(candidate) not in keys:
            return candidate
        n += 1


def _join(patient: str, label: str, suffix: str) -> str:
    p, t = sanitize_component(patient), sanitize_component(label)
    # Trim the label before the patient: exports are found by patient name.
    while _size(p, t, suffix) > MAX_FILENAME_BYTES and len(t) > 1:
        t = t[:-1].rstrip() or t[:1]
    while _size(p, t, suffix) > MAX_FILENAME_BYTES and len(p) > 1:
        p = p[:-1].rstrip() or p[:1]
    return f"{p}_{t}{suffix}"


def _size(patient: str, label: str, suffix: str) -> int:
    return len(f"{patient}_{label}{suffix}".encode())


def _key(name: str) -> str:
    return unicodedata.normalize("NFC", name).casefold()
