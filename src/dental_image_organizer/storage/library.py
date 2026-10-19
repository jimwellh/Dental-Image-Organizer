"""The library folder: patients and cases as plain folders (spec §4.11).

Lock order: always take a case lock before the I/O lock, never the reverse. A method that holds
the I/O lock must never wait for a case lock, otherwise two threads could each hold one and wait
for the other. `update_case` holds the case lock for the whole change but the I/O lock only
around its read and its write, so a slow change of one case never blocks reads of another.
`update_patient` renames a folder that holds many cases, so it takes all of their case locks
first, in sorted id order: two such callers then acquire in the same order and cannot deadlock.
"""

from __future__ import annotations

import os
import re
import shutil
import threading
import unicodedata
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, ExitStack, contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog

from dental_image_organizer.core.naming import sanitize_component
from dental_image_organizer.errors import (
    CaseExistsError,
    CaseNotFoundError,
    InvalidNameError,
    InvalidRequestError,
    PathOutsideLibraryError,
    PatientNotFoundError,
    StorageError,
)
from dental_image_organizer.storage.atomic import write_atomic
from dental_image_organizer.storage.schema import (
    CaseRecord,
    CaseType,
    CompositeRecord,
    PatientRecord,
    PhotoRecord,
    SequenceRecord,
    dump,
    new_id,
    parse_case,
    parse_patient,
)

PATIENT_FILE = "patient.json"
CASE_FILE = "case.json"
ORIGINALS_DIR = "originals"
EXPORT_DIR = "export"
TRASH_DIR = ".trash"
STAGING_PREFIX = ".importing-"

log = structlog.get_logger()

_DATE_PREFIX = re.compile(r"^\d{8}(?= )")
_Sig = tuple[int, int]


def local_now() -> datetime:
    return datetime.now().astimezone()


def folder_name(text: str) -> str:
    # A leading dot hides the folder in Finder and could collide with ".trash"; ".." must never
    # become a path. Trailing dots and spaces are rejected by Windows.
    name = sanitize_component(text).strip(". ")
    if not name:
        raise InvalidNameError(f"{text!r} is empty after removing dots and spaces from its ends")
    return name


class CaseLocks:
    """One re-entrant lock per case id, created on first use."""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._locks: dict[str, threading.RLock] = {}

    def hold(self, case_id: str) -> AbstractContextManager[None]:
        with self._guard:
            lock = self._locks.setdefault(case_id, threading.RLock())
        return _held(lock)


@contextmanager
def _held(lock: threading.RLock) -> Iterator[None]:
    with lock:
        yield


@dataclass(frozen=True)
class PatientEntry:
    record: PatientRecord
    folder: Path
    cases: tuple[CaseRecord, ...]


@dataclass(frozen=True)
class LibraryProblem:
    folder: str
    message: str


@dataclass(frozen=True)
class LibraryListing:
    patients: tuple[PatientEntry, ...]
    problems: tuple[LibraryProblem, ...]


@dataclass(frozen=True)
class _Parsed[T]:
    sig: _Sig
    record: T | None
    error: str | None


def _key(name: str) -> str:
    # APFS and NTFS are case-insensitive, and macOS hands names over decomposed.
    return unicodedata.normalize("NFC", name).casefold()


def _reason(exc: OSError) -> str:
    # strerror never carries the path, unlike str(exc).
    return exc.strerror or type(exc).__name__


def _sig(path: Path) -> _Sig | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


_ABSENT = (FileNotFoundError, NotADirectoryError)


def _child_dirs(parent: Path) -> tuple[list[Path], str | None]:
    """Sub-folders in sorted order, plus a reason when the folder exists but cannot be read."""
    try:
        with os.scandir(parent) as it:
            # Symlinks are skipped: a link that leaves the library would put reads and writes
            # of case.json outside the root.
            names = [
                e.name for e in it if not e.name.startswith(".") and e.is_dir(follow_symlinks=False)
            ]
    except _ABSENT:
        return [], None
    except OSError as exc:
        return [], f"The folder could not be read: {_reason(exc)}."
    return [parent / name for name in sorted(names)], None


class Library:
    def __init__(
        self,
        root: Path,
        locks: CaseLocks,
        *,
        clock: Callable[[], datetime] = local_now,
    ) -> None:
        self._root = root
        self._locks = locks
        self._clock = clock
        # Windows fails os.replace onto a file another thread has open, so every file read,
        # write, rename, and scan goes through this one lock.
        self._io = threading.RLock()
        self._files: dict[Path, _Parsed[Any]] = {}
        self._patient_dirs: dict[str, Path] = {}
        self._case_dirs: dict[str, Path] = {}
        self._listing = LibraryListing((), ())

    @property
    def root(self) -> Path:
        return self._root

    # -- index ---------------------------------------------------------------------------

    def listing(self) -> LibraryListing:
        return self._scan()

    def _parse[T](
        self, path: Path, parser: Callable[[bytes], T], seen: dict[Path, _Parsed[Any]]
    ) -> _Parsed[T] | None:
        try:
            st = path.stat()
        except _ABSENT:
            return None
        except OSError as exc:
            failed = _Parsed[T]((-1, -1), None, f"The file could not be read: {_reason(exc)}.")
            seen[path] = failed
            return failed
        sig = (st.st_mtime_ns, st.st_size)
        cached = self._files.get(path)
        if cached is not None and cached.sig == sig:
            seen[path] = cached
            return cached
        try:
            record, error = parser(path.read_bytes()), None
        except StorageError as exc:
            record, error = None, str(exc)
        except OSError as exc:
            record, error = None, f"The file could not be read: {_reason(exc)}."
        parsed = _Parsed(sig, record, error)
        seen[path] = parsed
        return parsed

    def _scan(self) -> LibraryListing:
        with self._io:
            seen: dict[Path, _Parsed[Any]] = {}
            problems: list[LibraryProblem] = []
            patients: list[PatientEntry] = []
            patient_dirs: dict[str, Path] = {}
            case_dirs: dict[str, Path] = {}
            pdirs, root_error = _child_dirs(self._root)
            if root_error:
                problems.append(LibraryProblem(".", root_error))
            for pdir in pdirs:
                parsed_patient = self._parse(pdir / PATIENT_FILE, parse_patient, seen)
                if parsed_patient is None:
                    continue  # not a folder of this app
                if parsed_patient.record is None:
                    problems.append(LibraryProblem(pdir.name, parsed_patient.error or ""))
                    continue
                patient = parsed_patient.record
                if patient.id in patient_dirs:
                    other = patient_dirs[patient.id].name
                    problems.append(LibraryProblem(pdir.name, _duplicate_message("patient", other)))
                    continue
                patient_dirs[patient.id] = pdir
                cases: list[CaseRecord] = []
                cdirs, patient_error = _child_dirs(pdir)
                if patient_error:
                    problems.append(LibraryProblem(pdir.name, patient_error))
                for cdir in cdirs:
                    parsed_case = self._parse(cdir / CASE_FILE, parse_case, seen)
                    if parsed_case is None:
                        continue
                    rel = f"{pdir.name}/{cdir.name}"
                    if parsed_case.record is None:
                        problems.append(LibraryProblem(rel, parsed_case.error or ""))
                        continue
                    case = parsed_case.record
                    if case.id in case_dirs:
                        other = f"{case_dirs[case.id].parent.name}/{case_dirs[case.id].name}"
                        problems.append(LibraryProblem(rel, _duplicate_message("case", other)))
                        continue
                    case_dirs[case.id] = cdir
                    cases.append(case)
                cases.sort(key=lambda c: c.created_at, reverse=True)
                patients.append(PatientEntry(patient, pdir, tuple(cases)))
            patients.sort(key=lambda e: (_key(e.record.name), e.folder.name))
            self._files = seen
            self._patient_dirs = patient_dirs
            self._case_dirs = case_dirs
            self._listing = LibraryListing(tuple(patients), tuple(problems))
            return self._listing

    def _fresh(self, path: Path) -> bool:
        cached = self._files.get(path)
        return cached is not None and cached.sig == _sig(path)

    def _case_dir(self, case_id: str) -> Path:
        # The index is trusted only while the file's signature matches; a folder renamed or
        # removed in Finder fails that check, and one rescan then finds it again or gives up.
        with self._io:
            for rescan in (False, True):
                if rescan:
                    self._scan()
                folder = self._case_dirs.get(case_id)
                if folder is not None and self._fresh(folder / CASE_FILE):
                    return folder
        raise CaseNotFoundError("This case no longer exists.")

    def _patient_dir(self, patient_id: str) -> Path:
        with self._io:
            for rescan in (False, True):
                if rescan:
                    self._scan()
                folder = self._patient_dirs.get(patient_id)
                if folder is not None and self._fresh(folder / PATIENT_FILE):
                    return folder
        raise PatientNotFoundError("This patient no longer exists.")

    def _remember(self, path: Path, record: PatientRecord | CaseRecord) -> None:
        # Our own write changes the signature; recording it avoids a rescan on the next lookup.
        sig = _sig(path)
        if sig is not None:
            self._files[path] = _Parsed(sig, record, None)

    # -- writes --------------------------------------------------------------------------

    def _write(self, path: Path, record: PatientRecord | CaseRecord, what: str) -> None:
        try:
            write_atomic(path, dump(record))
        except OSError as exc:
            raise StorageError(f"Saving the {what} failed: {_reason(exc)}") from exc
        self._remember(path, record)

    def _rename(self, source: Path, target: Path) -> None:
        try:
            source.rename(target)
        except OSError as exc:
            raise StorageError(
                f"Renaming the folder failed: {_reason(exc)}. "
                "Close any window that shows it and try again."
            ) from exc

    def _unique(self, parent: Path, base: str, *, exclude: Path | None = None) -> str:
        try:
            taken = {_key(p.name) for p in parent.iterdir() if p != exclude}
        except FileNotFoundError:
            taken = set()  # the folder does not exist yet, so nothing is taken
        except OSError as exc:
            # Guessing "nothing taken" here would overwrite another case's or patient's file.
            raise StorageError(f"Listing the folder failed: {_reason(exc)}") from exc
        name, n = base, 2
        while _key(name) in taken:
            name = f"{base} ({n})"
            n += 1
        return name

    # -- patients ------------------------------------------------------------------------

    def patient(self, patient_id: str) -> PatientEntry:
        with self._io:
            for entry in self._scan().patients:
                if entry.record.id == patient_id:
                    return entry
        raise PatientNotFoundError("This patient no longer exists.")

    def create_patient(self, name: str, note: str | None) -> PatientRecord:
        display = unicodedata.normalize("NFC", name).strip()
        base = folder_name(display)
        with self._io:
            folder = self._root / self._unique(self._root, base)
            record = PatientRecord(id=new_id(), name=display, note=note, created_at=self._clock())
            self._write(folder / PATIENT_FILE, record, "patient")
            return record

    def update_patient(self, patient_id: str, *, name: str, note: str | None) -> PatientRecord:
        display = unicodedata.normalize("NFC", name).strip()
        base = folder_name(display)
        while True:
            ids = sorted(c.id for c in self.patient(patient_id).cases)
            with ExitStack() as stack:
                for case_id in ids:
                    stack.enter_context(self._locks.hold(case_id))
                with self._io:
                    entry = self.patient(patient_id)
                    if sorted(c.id for c in entry.cases) != ids:
                        continue  # a case appeared meanwhile; lock it too
                    folder, current = entry.folder, entry.record
                    target = folder.name
                    if base != folder.name:
                        target = self._unique(self._root, base, exclude=folder)
                    if target != folder.name:
                        self._rename(folder, self._root / target)
                    record = PatientRecord(
                        id=current.id, name=display, note=note, created_at=current.created_at
                    )
                    self._write(self._root / target / PATIENT_FILE, record, "patient")
                    return record

    def adopt_patient(self, record: PatientRecord) -> PatientEntry:
        """Create the folder for a patient that keeps the id and dates of an imported record."""
        display = unicodedata.normalize("NFC", record.name).strip()
        base = folder_name(display)
        with self._io:
            # Two imports of one archive may both have seen no patient: one id, one folder.
            try:
                return self.patient(record.id)
            except PatientNotFoundError:
                pass
            folder = self._root / self._unique(self._root, base)
            self._write(folder / PATIENT_FILE, record, "patient")
            self._patient_dirs[record.id] = folder
            return PatientEntry(record, folder, ())

    def remove_empty_patient(self, patient_id: str) -> None:
        """Undo `adopt_patient` after a failed import. A folder that holds anything else stays."""
        with self._io:
            try:
                folder = self._patient_dir(patient_id)
                if [p.name for p in folder.iterdir()] != [PATIENT_FILE]:
                    return
                (folder / PATIENT_FILE).unlink()
                folder.rmdir()
            except PatientNotFoundError:
                return
            except OSError as exc:
                log.warning("patient_rollback_failed", reason=_reason(exc))
                return
            self._patient_dirs.pop(patient_id, None)
            self._files.pop(folder / PATIENT_FILE, None)

    # -- cases ---------------------------------------------------------------------------

    def create_case(
        self,
        patient_id: str,
        kind: CaseType,
        title: str,
        *,
        composite: CompositeRecord | None = None,
    ) -> CaseRecord:
        if (kind == "composite") != (composite is not None):
            raise InvalidRequestError(
                "A composite case needs composite settings; a procedure case has none."
            )
        display = unicodedata.normalize("NFC", title).strip()
        base = folder_name(display)
        now = self._clock()
        with self._io:
            pdir = self._patient_dir(patient_id)
            folder = pdir / self._unique(pdir, f"{now:%Y%m%d} {base}")
            record = CaseRecord(
                id=new_id(),
                type=kind,
                patient_id=patient_id,
                title=display,
                output_scale=1.0 if kind == "procedure" else None,
                created_at=now,
                updated_at=now,
                sequence=SequenceRecord() if kind == "procedure" else None,
                composite=composite,
            )
            self._write(folder / CASE_FILE, record, "case")
            return record

    def staging_dir(self) -> Path:
        """A fresh folder inside the root, so moving a finished case in is a rename."""
        folder = self._root / f"{STAGING_PREFIX}{new_id()}"
        try:
            folder.mkdir(parents=True)
        except OSError as exc:
            raise StorageError(f"Creating the folder failed: {_reason(exc)}") from exc
        return folder

    def clean_staging(self) -> None:
        """Remove folders an interrupted import left behind. Call it only while holding the
        instance lock: another process's import is otherwise still using its folder."""
        with self._io:
            try:
                leftovers = [
                    Path(e.path)
                    for e in os.scandir(self._root)
                    if e.name.startswith(STAGING_PREFIX) and e.is_dir(follow_symlinks=False)
                ]
            except _ABSENT:
                return
            except OSError as exc:
                log.warning("staging_scan_failed", reason=_reason(exc))
                return
            for folder in leftovers:
                try:
                    shutil.rmtree(folder)
                except OSError as exc:
                    # Housekeeping only: a locked leftover must not block startup.
                    log.warning("staging_remove_failed", reason=_reason(exc))

    def install_case(self, staged: Path, record: CaseRecord) -> CaseRecord:
        """Move a finished case folder (case.json and originals/) under its patient."""
        base = (
            f"{record.created_at:%Y%m%d} {folder_name(unicodedata.normalize('NFC', record.title))}"
        )
        with self._locks.hold(record.id), self._io:
            try:
                existing = self.read_case(record.id)
            except CaseNotFoundError:
                pass
            else:
                raise CaseExistsError(
                    "This case is already in the library.",
                    details={"case_id": existing.id, "title": existing.title},
                )
            pdir = self._patient_dir(record.patient_id)
            folder = pdir / self._unique(pdir, base)
            try:
                os.replace(staged, folder)
            except OSError as exc:
                raise StorageError(
                    f"Moving the case into the library failed: {_reason(exc)}"
                ) from exc
            self._remember(folder / CASE_FILE, record)
            self._case_dirs[record.id] = folder
            return record

    def case_dir(self, case_id: str) -> Path:
        return self._case_dir(case_id)

    def read_case(self, case_id: str) -> CaseRecord:
        with self._io:
            path = self._case_dir(case_id) / CASE_FILE
            try:
                return parse_case(path.read_bytes())
            except FileNotFoundError as exc:
                raise CaseNotFoundError("This case no longer exists.") from exc
            except OSError as exc:
                raise StorageError(f"The case could not be read: {_reason(exc)}") from exc

    def update_case(self, case_id: str, change: Callable[[CaseRecord], CaseRecord]) -> CaseRecord:
        with self._locks.hold(case_id):
            current = self.read_case(case_id)
            # `change` may be slow (a composite replan); the I/O lock is not held meanwhile.
            updated = change(current).model_copy(update={"updated_at": self._clock()}).validated()
            with self._io:
                self._write(self._case_dir(case_id) / CASE_FILE, updated, "case")
            return updated

    def rename_case(self, case_id: str, title: str) -> CaseRecord:
        display = unicodedata.normalize("NFC", title).strip()
        base = folder_name(display)
        with self._locks.hold(case_id):
            with self._io:
                folder = self._case_dir(case_id)
                match = _DATE_PREFIX.match(folder.name)
                prefix = match.group() if match else f"{self.read_case(case_id).created_at:%Y%m%d}"
                target = self._unique(folder.parent, f"{prefix} {base}", exclude=folder)
                if target != folder.name:
                    self._rename(folder, folder.parent / target)
            return self.update_case(case_id, lambda c: c.model_copy(update={"title": display}))

    def trash_case(self, case_id: str) -> Path:
        with self._locks.hold(case_id), self._io:
            folder = self._case_dir(case_id)
            patient_dir = folder.parent
            trash_patient = self._root / TRASH_DIR / patient_dir.name
            try:
                trash_patient.mkdir(parents=True, exist_ok=True)
                copy = trash_patient / PATIENT_FILE
                # Kept beside the case so moving it back in Finder restores it whole.
                if not copy.exists():
                    write_atomic(copy, (patient_dir / PATIENT_FILE).read_bytes())
            except OSError as exc:
                raise StorageError(f"Saving the case failed: {_reason(exc)}") from exc
            target = trash_patient / self._unique(trash_patient, folder.name)
            self._rename(folder, target)
            return target

    # -- paths ---------------------------------------------------------------------------

    def originals_dir(self, case_id: str) -> Path:
        return self._made(self.case_dir(case_id) / ORIGINALS_DIR)

    def export_dir(self, case_id: str) -> Path:
        return self._made(self.case_dir(case_id) / EXPORT_DIR)

    def _made(self, folder: Path) -> Path:
        try:
            folder.mkdir(exist_ok=True)
        except OSError as exc:
            raise StorageError(f"Creating the folder failed: {_reason(exc)}") from exc
        return folder

    def original_path(self, case_id: str, photo: PhotoRecord) -> Path:
        return self.resolve(self.case_dir(case_id) / photo.file)

    def resolve(self, path: str | Path) -> Path:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self._root / candidate
        resolved = candidate.resolve()
        if not resolved.is_relative_to(self._root.resolve()):
            raise PathOutsideLibraryError("The path is outside the library.")
        return resolved


def _duplicate_message(kind: str, other: str) -> str:
    return (
        f"This folder duplicates the {kind} in “{other}”; "
        "remove one copy, or export it and import it as a copy."
    )
