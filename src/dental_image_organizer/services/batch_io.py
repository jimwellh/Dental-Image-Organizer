"""File helpers shared by the headless batches (spec §4.10, §4.13)."""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

import structlog

from dental_image_organizer.core.imageio import DecodedImage, decode, read_capture_time
from dental_image_organizer.core.naming import unique_name
from dental_image_organizer.errors import DioError

log = structlog.get_logger(__name__)

MTIME_WARNING = "No capture time in EXIF; used the file modification time."


def load_photo(path: Path) -> DecodedImage:
    return decode(path.read_bytes())


def load_photo_with_id(path: Path) -> tuple[DecodedImage, str]:
    """Decode a photo, with the SHA-256 of its bytes: an id that carries no file name."""
    data = path.read_bytes()
    photo = decode(data)
    return photo, hashlib.sha256(data).hexdigest()


def os_error_message(exc: OSError) -> str:
    # str(exc) embeds the path when the error carries one, and patient folders and exports
    # carry patient names. Pillow's encoder errors carry no path and keep their message.
    if exc.filename is None and exc.filename2 is None:
        return str(exc) or type(exc).__name__
    return exc.strerror or type(exc).__name__


def unexpected_message(exc: Exception) -> str:
    detail = str(exc).strip()
    return f"Unexpected {type(exc).__name__}" + (f": {detail}" if detail else "")


def failure_message(exc: Exception) -> str:
    """The message reported for a photo that failed; the caller logs the trace."""
    if isinstance(exc, DioError):
        return str(exc)
    return os_error_message(exc) if isinstance(exc, OSError) else unexpected_message(exc)


def capture_time(image: DecodedImage, path: Path) -> tuple[datetime, bool]:
    """The EXIF capture time, or the file's modification time; True marks the fallback."""
    if image.metadata.captured_at is not None:
        return image.metadata.captured_at, False
    log.warning("capture_time_from_file")
    return datetime.fromtimestamp(path.stat().st_mtime), True


def header_capture_time(path: Path) -> datetime | None:
    """The EXIF capture time left in the header of a photo that failed to decode, if any."""
    try:
        return read_capture_time(path.read_bytes())
    except OSError:
        return None


def write_unique(out_dir: Path, taken: set[str], name: str, data: bytes) -> Path:
    """Write under `name`, or `name_2`, `name_3`, ... so an existing file is never replaced."""
    final = unique_name(name, taken)
    taken.add(final)
    path = out_dir / final
    path.write_bytes(data)
    return path
