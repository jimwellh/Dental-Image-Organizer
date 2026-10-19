"""Case archives: one zip with `patient.json`, `case.json`, and the originals (spec §4.11).

Importing is all-or-nothing: the archive is read into a staging folder inside the library,
checked in full there, and only then moved into place, so a bad archive leaves no trace.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import shutil
import zipfile
import zlib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import uuid4

from dental_image_organizer.errors import (
    CaseExistsError,
    CaseNotFoundError,
    InvalidArchiveError,
    PatientNotFoundError,
    StorageError,
)
from dental_image_organizer.storage.atomic import write_atomic
from dental_image_organizer.storage.library import CASE_FILE, PATIENT_FILE, Library
from dental_image_organizer.storage.schema import (
    ORIGINAL_FILE_PATTERN,
    CaseRecord,
    PatientRecord,
    dump,
    new_id,
    parse_case,
    parse_patient,
)

ConflictPolicy = Literal["ask", "skip", "copy"]

MAX_ORIGINAL_BYTES = 200 * 1024 * 1024
MAX_JSON_BYTES = 16 * 1024 * 1024
_CHUNK = 1024 * 1024
_ORIGINAL = re.compile(ORIGINAL_FILE_PATTERN)
_MISMATCH = "The archive is damaged: a photo does not match its record."
# Everything a damaged or hostile member can raise while it is being read.
_READ_ERRORS = (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError, RuntimeError)


@dataclass(frozen=True)
class ImportOutcome:
    status: Literal["imported", "skipped"]
    case_id: str
    patient_id: str
    title: str


def write_case_archive(case_dir: Path, out: Path) -> None:
    patient_json = (case_dir.parent / PATIENT_FILE).read_bytes()
    case_json = (case_dir / CASE_FILE).read_bytes()
    case = parse_case(case_json)
    # Written beside `out` and replaced into it, so a crash never leaves half a zip.
    tmp = out.with_name(f".{out.name}.{uuid4().hex[:8]}.tmp")
    try:
        with zipfile.ZipFile(tmp, "w") as zf:
            zf.writestr(PATIENT_FILE, patient_json, compress_type=zipfile.ZIP_DEFLATED)
            zf.writestr(CASE_FILE, case_json, compress_type=zipfile.ZIP_DEFLATED)
            # Originals are JPEG/HEIC/PNG already, so deflating them only costs time.
            for name in dict.fromkeys(p.file for p in case.photos):
                try:
                    zf.write(case_dir / name, name, compress_type=zipfile.ZIP_STORED)
                except FileNotFoundError as exc:
                    raise StorageError(
                        "An original photo of this case is missing from its folder."
                    ) from exc
        os.replace(tmp, out)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise


def import_case_archive(
    library: Library, archive: Path, *, on_conflict: ConflictPolicy = "ask"
) -> ImportOutcome:
    try:
        zf = zipfile.ZipFile(archive)
    except zipfile.BadZipFile as exc:
        raise InvalidArchiveError("This file is not a case archive.") from exc
    with zf:
        infos = _check_members(zf)
        patient, case = _read_records(zf, infos)
        staging = library.staging_dir()
        try:
            _extract_originals(zf, infos, case, staging)
            return _install(library, staging, patient, case, on_conflict)
        finally:
            shutil.rmtree(staging, ignore_errors=True)


def _limit(name: str) -> int:
    return MAX_ORIGINAL_BYTES if _ORIGINAL.fullmatch(name) else MAX_JSON_BYTES


def _too_large(name: str) -> InvalidArchiveError:
    return InvalidArchiveError(f"The archive holds a file that is too large: {_shown(name)}.")


def _shown(name: str) -> str:
    return repr(name if len(name) <= 80 else f"{name[:80]}...")


def _check_members(zf: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    # The allow-list also rejects "../", absolute paths, and folders: none match the pattern.
    allowed = {PATIENT_FILE, CASE_FILE}
    infos = zf.infolist()
    for info in infos:
        if info.filename not in allowed and not _ORIGINAL.fullmatch(info.filename):
            raise InvalidArchiveError(
                f"The archive holds a file that does not belong: {_shown(info.filename)}."
            )
    counts = Counter(i.filename for i in infos)
    for name, count in counts.items():
        if count > 1:
            raise InvalidArchiveError(f"The archive lists {_shown(name)} more than once.")
    for info in infos:
        if info.file_size > _limit(info.filename):
            raise _too_large(info.filename)
    return {i.filename: i for i in infos}


def _read_records(
    zf: zipfile.ZipFile, infos: dict[str, zipfile.ZipInfo]
) -> tuple[PatientRecord, CaseRecord]:
    for name in (PATIENT_FILE, CASE_FILE):
        if name not in infos:
            raise InvalidArchiveError(f"The archive has no {name}.")
    try:
        patient = parse_patient(_read_json(zf, infos[PATIENT_FILE]))
        case = parse_case(_read_json(zf, infos[CASE_FILE]))
    except StorageError as exc:
        # The wording stays: a newer schema must still say "newer version".
        raise InvalidArchiveError(str(exc)) from exc
    if case.patient_id != patient.id:
        raise InvalidArchiveError("The archive is damaged: the case belongs to another patient.")
    return patient, case


def _read_json(zf: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    chunks: list[bytes] = []
    total = 0
    try:
        with zf.open(info) as src:
            while chunk := src.read(_CHUNK):
                total += len(chunk)
                if total > MAX_JSON_BYTES:
                    raise _too_large(info.filename)
                chunks.append(chunk)
    except _READ_ERRORS as exc:
        raise InvalidArchiveError("The archive is damaged: a file cannot be read.") from exc
    return b"".join(chunks)


def _extract_originals(
    zf: zipfile.ZipFile, infos: dict[str, zipfile.ZipInfo], case: CaseRecord, staging: Path
) -> None:
    expected: dict[str, set[str]] = {}
    for photo in case.photos:
        expected.setdefault(photo.file, set()).add(photo.sha256)
    for name, digests in expected.items():
        # An original that no record references is left in the zip and never extracted.
        if name not in infos or len(digests) != 1:
            raise InvalidArchiveError(_MISMATCH)
        target = staging / name
        target.parent.mkdir(exist_ok=True)
        if _copy_capped(zf, infos[name], target, _limit(name)) != next(iter(digests)):
            raise InvalidArchiveError(_MISMATCH)


def _copy_capped(zf: zipfile.ZipFile, info: zipfile.ZipInfo, target: Path, limit: int) -> str:
    """Stream one member to `target` and return its SHA-256.

    The header size was checked already, but a header can lie, so the bytes actually read are
    capped as well.
    """
    digest = hashlib.sha256()
    total = 0
    try:
        with zf.open(info) as src, target.open("xb") as dst:
            while chunk := src.read(_CHUNK):
                total += len(chunk)
                if total > limit:
                    raise _too_large(info.filename)
                digest.update(chunk)
                dst.write(chunk)
    except _READ_ERRORS as exc:
        raise InvalidArchiveError("The archive is damaged: a file cannot be read.") from exc
    return digest.hexdigest()


def _install(
    library: Library,
    staging: Path,
    patient: PatientRecord,
    case: CaseRecord,
    on_conflict: ConflictPolicy,
) -> ImportOutcome:
    existing = _find_case(library, case.id)
    record = case.model_copy(update={"exports": []})  # exports are regenerated, not archived
    if existing is not None:
        if on_conflict == "ask":
            raise CaseExistsError(
                "This case is already in the library.",
                details={"case_id": existing.id, "title": existing.title},
            )
        if on_conflict == "skip":
            return ImportOutcome("skipped", existing.id, existing.patient_id, existing.title)
        record = record.model_copy(update={"id": new_id()})
    record = record.validated()
    write_atomic(staging / CASE_FILE, dump(record))
    try:
        library.patient(patient.id)
    except PatientNotFoundError:
        library.adopt_patient(patient)
    library.install_case(staging, record)
    return ImportOutcome("imported", record.id, record.patient_id, record.title)


def _find_case(library: Library, case_id: str) -> CaseRecord | None:
    try:
        return library.read_case(case_id)
    except CaseNotFoundError:
        return None
