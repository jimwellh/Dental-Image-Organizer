"""Uploads into a case: content-addressed originals and per-file skip reasons (spec §3.1, §4.11).

Each upload is streamed into a hidden part file while it is hashed, so a 200 MB photo never sits
in memory, and then renamed to `<sha256[:16]>.<ext>`. Identical content therefore lands on one
file, and a photo that is already in the case is reported instead of added twice.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import unicodedata
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, Literal

import structlog
from structlog.contextvars import bound_contextvars

from dental_image_organizer.core.imageio import ImageFormat, sniff_format
from dental_image_organizer.errors import StorageError, UnsupportedFormatError
from dental_image_organizer.storage.library import ORIGINALS_DIR, local_now
from dental_image_organizer.storage.schema import CaseRecord, PhotoRecord, Role, new_id

if TYPE_CHECKING:
    from dental_image_organizer.services.context import AppContext

log = structlog.get_logger(__name__)

MAX_UPLOAD_BYTES = 200 * 1024 * 1024
SNIFF_BYTES = 4096
COPY_CHUNK = 1 << 20
MAX_NAME_CHARS = 255

UploadSkipReason = Literal["video", "raw", "sidecar", "unknown", "duplicate", "too_large"]

_EXTENSIONS: dict[ImageFormat, str] = {"heic": "heic", "jpeg": "jpg", "png": "png"}
_SEPARATORS = re.compile(r"[/\\]")
# Cc are control characters; Cs are lone surrogates, which UTF-8 JSON cannot hold, so a name
# carrying one would make the whole case.json write fail.
_DROPPED_CATEGORIES = frozenset({"Cc", "Cs"})
TOO_LARGE_MESSAGE = "Files over 200 MB are not accepted."
DUPLICATE_MESSAGE = "This photo is already in the case."


@dataclass(frozen=True)
class Upload:
    name: str
    stream: BinaryIO
    last_modified: datetime | None = None


@dataclass(frozen=True)
class Skipped:
    name: str
    reason: UploadSkipReason
    message: str


@dataclass(frozen=True)
class Staged:
    name: str
    sha256: str
    fmt: ImageFormat
    file_name: str
    last_modified: datetime | None


@dataclass(frozen=True)
class UploadOutcome:
    job_id: str | None
    added: tuple[str, ...]
    skipped: tuple[Skipped, ...]


def display_name(raw: str) -> str:
    """The name shown for an upload. It is only ever displayed, never used as a path."""
    last = _SEPARATORS.split(raw)[-1]
    text = unicodedata.normalize("NFC", last)
    kept = "".join(ch for ch in text if unicodedata.category(ch) not in _DROPPED_CATEGORIES)
    return kept[:MAX_NAME_CHARS] or "photo"


def stage_upload(upload: Upload, directory: Path) -> Staged | Skipped:
    """Copy one upload into `directory` under its content name, or say why it was skipped."""
    name = display_name(upload.name)
    head = upload.stream.read(SNIFF_BYTES)
    try:
        fmt = sniff_format(head)
    except UnsupportedFormatError as exc:
        # Returning before the copy leaves the rest of a video unread.
        return Skipped(name, exc.reason, str(exc))
    part = directory / f".{uuid.uuid4().hex}.part"
    try:
        sha256 = _copy(head, upload.stream, part)
        if sha256 is None:
            return Skipped(name, "too_large", TOO_LARGE_MESSAGE)
        file_name = f"{sha256[:16]}.{_EXTENSIONS[fmt]}"
        _publish(part, directory / file_name)
        return Staged(name, sha256, fmt, file_name, upload.last_modified)
    except OSError as exc:
        raise StorageError(f"Saving {name} failed: {exc.strerror or type(exc).__name__}") from exc
    finally:
        # Gone after a successful rename; otherwise a skipped, failed, or redundant copy.
        with contextlib.suppress(OSError):
            part.unlink(missing_ok=True)


def _copy(head: bytes, stream: BinaryIO, part: Path) -> str | None:
    """Write and hash the stream into `part`; None as soon as it passes the size limit."""
    digest = hashlib.sha256()
    size = 0
    with part.open("xb") as out:
        chunk = head
        while chunk:
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                return None
            digest.update(chunk)
            out.write(chunk)
            chunk = stream.read(COPY_CHUNK)
        out.flush()
        # The case records the photo right after; its bytes must be on disk before that.
        os.fsync(out.fileno())
    return digest.hexdigest()


def _publish(part: Path, target: Path) -> None:
    try:
        os.replace(part, target)
    except PermissionError:
        # Windows refuses to replace a file another thread has open. The name is the content
        # hash, so an existing file already holds these bytes.
        if not target.exists():
            raise


def add_photos(
    ctx: AppContext, case_id: str, uploads: Sequence[Upload], *, role: Role
) -> tuple[list[str], list[Skipped]]:
    """Store the uploads as `pending` photos of the case; return the new ids and the skips."""
    with bound_contextvars(case_id=case_id):
        library = ctx.library()
        directory = library.originals_dir(case_id)
        # Staged outside the case lock: copying and hashing large files must not block the
        # case's other updates. Names are content hashes, so a file a record already uses is
        # only ever replaced by the same bytes.
        outcomes = [stage_upload(upload, directory) for upload in uploads]
        added: list[str] = []
        skipped: list[Skipped] = []

        def change(case: CaseRecord) -> CaseRecord:
            added.clear()
            skipped.clear()
            known = {p.sha256 for p in case.photos}
            now = local_now()
            photos = list(case.photos)
            for outcome in outcomes:
                if isinstance(outcome, Skipped):
                    skipped.append(outcome)
                elif outcome.sha256 in known:
                    skipped.append(Skipped(outcome.name, "duplicate", DUPLICATE_MESSAGE))
                else:
                    known.add(outcome.sha256)
                    photo = _pending(outcome, role, now)
                    photos.append(photo)
                    added.append(photo.id)
            return case.model_copy(update={"photos": photos})

        if any(isinstance(o, Staged) for o in outcomes):
            library.update_case(case_id, change)
        else:
            skipped.extend(o for o in outcomes if isinstance(o, Skipped))
        log.info("photos_added", added=len(added), skipped=len(skipped))
        return added, skipped


def _pending(staged: Staged, role: Role, now: datetime) -> PhotoRecord:
    return PhotoRecord(
        id=new_id(),
        sha256=staged.sha256,
        file=f"{ORIGINALS_DIR}/{staged.file_name}",
        source_name=staged.name,
        # Provisional until decoding finds an EXIF capture time (spec §4.4 step 5).
        captured_at=staged.last_modified or now,
        captured_at_source="file_mtime",
        role=role,
        stage=None,
        slot=None,
        state="pending",
    )
