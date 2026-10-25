"""Write the app's OpenAPI schema, the single source of the frontend's TypeScript types.

The frontend's `npm run gen:api` runs this, and CI reruns it to prove the committed schema and
generated types are current. It builds the real app over a throw-away folder, because the schema
comes from the route and model definitions and must not depend on, or create anything in, the
developer's own library. The output is sorted and LF-terminated so the same code gives the same
bytes on every machine.

Run with:  uv run python scripts/export_openapi.py frontend/src/api/openapi.json
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from dental_image_organizer.api.app import create_app
from dental_image_organizer.paths import APP_DIR_ENV, AppDirs
from dental_image_organizer.services.context import create_context

# The schema does not depend on the port; any valid one builds the app.
PORT = 8765


@contextmanager
def _app_dir_override(root: Path) -> Iterator[None]:
    # The default library root is read from the environment, so pointing it at the throw-away
    # folder keeps `create_context` from creating ~/DentalImageOrganizer.
    previous = os.environ.get(APP_DIR_ENV)
    os.environ[APP_DIR_ENV] = str(root)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(APP_DIR_ENV, None)
        else:
            os.environ[APP_DIR_ENV] = previous


def openapi_document() -> dict[str, Any]:
    with TemporaryDirectory(prefix="dio-openapi-") as tmp, _app_dir_override(Path(tmp)):
        root = Path(tmp)
        dirs = AppDirs(root / "config", root / "data", root / "cache", root / "log")
        ctx = create_context(dirs, workers=1)
        try:
            app = create_app(ctx, port=PORT, shutdown=lambda: None, web_dir=None)
            document: dict[str, Any] = app.openapi()
            return document
        finally:
            ctx.close()


def render(document: dict[str, Any]) -> bytes:
    # Bytes, not text mode: Windows would turn "\n" into "\r\n" and the CI diff would never match.
    text = json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    return text.encode("utf-8")


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        sys.stderr.write("usage: export_openapi.py OUTPUT.json\n")
        return 2
    Path(args[0]).write_bytes(render(openapi_document()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
