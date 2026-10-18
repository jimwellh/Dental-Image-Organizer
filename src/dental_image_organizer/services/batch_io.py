"""File helpers shared by the headless batches (spec §4.10, §4.13)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import structlog

from dental_image_organizer.core.imageio import DecodedImage, decode
from dental_image_organizer.core.naming import unique_name

log = structlog.get_logger(__name__)

MTIME_WARNING = "No capture time in EXIF; used the file modification time."


def load_photo(path: Path) -> DecodedImage:
    return decode(path.read_bytes())


def os_error_message(exc: OSError) -> str:
    # str(exc) embeds the path when the error carries one, and patient folders and exports
    # carry patient names. Pillow's encoder errors carry no path and keep their message.
    if exc.filename is None and exc.filename2 is None:
        return str(exc) or type(exc).__name__
    return exc.strerror or type(exc).__name__


def unexpected_message(exc: Exception) -> str:
    detail = str(exc).strip()
    return f"Unexpected {type(exc).__name__}" + (f": {detail}" if detail else "")


def capture_time(image: DecodedImage, path: Path) -> tuple[datetime, bool]:
    """The EXIF capture time, or the file's modification time; True marks the fallback."""
    if image.metadata.captured_at is not None:
        return image.metadata.captured_at, False
    log.warning("capture_time_from_file")
    return datetime.fromtimestamp(path.stat().st_mtime), True


def write_unique(out_dir: Path, taken: set[str], name: str, data: bytes) -> Path:
    """Write under `name`, or `name_2`, `name_3`, ... so an existing file is never replaced."""
    final = unique_name(name, taken)
    taken.add(final)
    path = out_dir / final
    path.write_bytes(data)
    return path
