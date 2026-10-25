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
    InvalidNameError,
    PatientNotFoundError,
    StorageError,
)
from dental_image_organizer.storage.atomic import write_atomic
from dental_image_organizer.storage.library import (
    CASE_FILE,
    PATIENT_FILE,
    Library,
    folder_name,
)
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
_LINKED_ORIGINAL = (
    "An original photo of this case is a link to a file elsewhere; "
    "put the photo itself in the case's originals folder."
)
# Everything a damaged or hostile member can raise while it is being read.
_READ_ERRORS = (
    zipfile.BadZipFile,
    zlib.error,
    EOFError,
    NotImplementedError,
    RuntimeError,
    UnicodeDecodeError,
)


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
    inside = case_dir.resolve()
    # Written beside `out` and replaced into it, so a crash never leaves half a zip.
    tmp = out.with_name(f".{out.name}.{uuid4().hex[:8]}.tmp")
    try:
        with tmp.open("wb") as f:
            with zipfile.ZipFile(f, "w") as zf:
                zf.writestr(PATIENT_FILE, patient_json, compress_type=zipfile.ZIP_DEFLATED)
                zf.writestr(CASE_FILE, case_json, compress_type=zipfile.ZIP_DEFLATED)
                # Originals are JPEG/HEIC/PNG already, so deflating them only costs time.
                for name in dict.fromkeys(p.file for p in case.photos):
                    source = case_dir / name
                    # A link, of the file or of its folder, would pack a file from elsewhere on
                    # the disk into an archive that is made to be passed on.
                    if source.is_symlink() or not source.resolve().is_relative_to(inside):
                        raise StorageError(_LINKED_ORIGINAL)
                    try:
                        zf.write(source, name, compress_type=zipfile.ZIP_STORED)
                    except FileNotFoundError as exc:
                        raise StorageError(
                            "An original photo of this case is missing from its folder."
                        ) from exc
            # Without the fsync a power loss could leave a truncated zip under the final name.
            f.flush()
            os.fsync(f.fileno())
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
    except (zipfile.BadZipFile, UnicodeDecodeError, EOFError) as exc:
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
    # Checked now so an unusable name cannot fail after the patient folder was created.
    for what, text in (("patient name", patient.name), ("case title", case.title)):
        try:
            folder_name(text)
        except InvalidNameError as exc:
            raise InvalidArchiveError(
                f"The archive is damaged: the {what} cannot be used as a folder name."
            ) from exc
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
        if on_conflict != "copy":
            return _conflict(on_conflict, existing.id, existing.patient_id, existing.title)
        record = record.model_copy(update={"id": new_id()})
    record = record.validated()
    write_atomic(staging / CASE_FILE, dump(record))
    adopted: str | None = None
    try:
        library.patient_record(patient.id)
    except PatientNotFoundError:
        entry = library.adopt_patient(patient)
        # adopt_patient hands back the existing entry when another import won the race, and
        # that one is not ours to undo.
        if entry.record is patient:
            adopted = patient.id
    try:
        library.install_case(staging, record)
    except CaseExistsError as exc:
        # Another import installed this case after the check above.
        _undo_patient(library, adopted)
        return _conflict(
            on_conflict, exc.details["case_id"], record.patient_id, exc.details["title"]
        )
    except BaseException:
        _undo_patient(library, adopted)
        raise
    return ImportOutcome("imported", record.id, record.patient_id, record.title)


def _undo_patient(library: Library, patient_id: str | None) -> None:
    if patient_id is not None:
        library.remove_empty_patient(patient_id)


def _conflict(policy: ConflictPolicy, case_id: str, patient_id: str, title: str) -> ImportOutcome:
    if policy == "skip":
        return ImportOutcome("skipped", case_id, patient_id, title)
    raise CaseExistsError(
        "This case is already in the library.", details={"case_id": case_id, "title": title}
    )


def _find_case(library: Library, case_id: str) -> CaseRecord | None:
    try:
        return library.read_case(case_id)
    except CaseNotFoundError:
        return None
