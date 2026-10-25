"""Crash-safe file writes."""

import contextlib
import os
import time
from pathlib import Path
from uuid import uuid4

# On Windows an antivirus or indexer may hold the target open for a moment, which makes
# os.replace fail with PermissionError; a short retry rides that out.
REPLACE_ATTEMPTS = 20
REPLACE_DELAY_S = 0.05


def write_atomic(path: Path, data: bytes) -> None:
    """Write `data` so readers see the old file or the new one, never a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # A unique name keeps two writers of one file from sharing a temporary file.
    tmp = path.with_name(f".{path.name}.{uuid4().hex[:8]}.tmp")
    try:
        with tmp.open("wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        for attempt in range(REPLACE_ATTEMPTS):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == REPLACE_ATTEMPTS - 1:
                    raise
                time.sleep(REPLACE_DELAY_S)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise
