"""`scripts/export_openapi.py` writes a stable schema without touching a real folder."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from dental_image_organizer.paths import APP_DIR_ENV

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "export_openapi.py"
RUN_TIMEOUT_S = 120


def run_export(out: Path, home: Path) -> None:
    # Windows needs the rest of the environment (SYSTEMROOT), so only the home folder and the
    # app-folder override change: the script must not reach the real home or a caller's override.
    env = {k: v for k, v in os.environ.items() if k != APP_DIR_ENV}
    env.update(HOME=str(home), USERPROFILE=str(home))
    subprocess.run(
        [sys.executable, str(SCRIPT.relative_to(REPO_ROOT)), str(out)],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        timeout=RUN_TIMEOUT_S,
        capture_output=True,
    )


def test_export_is_deterministic_json_with_lf(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    first, second = tmp_path / "first.json", tmp_path / "second.json"

    run_export(first, home)
    run_export(second, home)

    raw = first.read_bytes()
    assert raw == second.read_bytes()
    assert b"\r\n" not in raw
    assert raw.endswith(b"\n")
    document = json.loads(raw)
    assert "/api/cases/{case_id}/photos" in document["paths"]
    assert "CaseView" in document["components"]["schemas"]


def test_export_touches_no_real_folder(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()

    run_export(tmp_path / "openapi.json", home)

    assert not list(home.rglob("DentalImageOrganizer"))
