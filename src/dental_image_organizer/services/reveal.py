"""Show a library path in Finder or Explorer (spec §4.12 `POST /system/reveal`)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from dental_image_organizer.errors import DioError


def reveal(path: Path) -> None:
    if sys.platform == "darwin":
        command = ["open", "-R", str(path)]
    elif sys.platform == "win32":
        command = ["explorer", f"/select,{path}"]
    else:
        # xdg-open cannot select an item, so it opens the folder itself, or the one holding a file.
        command = ["xdg-open", str(path if path.is_dir() else path.parent)]
    try:
        # Never waited for: Explorer exits with 1 even when it succeeds.
        subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    except OSError as exc:
        raise DioError(
            f"Revealing the folder failed: {exc.strerror or type(exc).__name__}"
        ) from exc
