"""Alignment metrics on real, de-identified photos (spec §8.3). Never runs in CI.

Layout: local_fixtures/procedure/<case>/reference.<ext> plus that case's other photos.
Each run writes local_fixtures/reports/procedure_<case>.csv for threshold tuning.
"""

import csv
from pathlib import Path

import pytest

from dental_image_organizer.core.align import prepare_reference
from dental_image_organizer.core.imageio import decode
from dental_image_organizer.errors import UnsupportedFormatError
from dental_image_organizer.services.procedure import compute_edit

ROOT = Path(__file__).resolve().parents[2] / "local_fixtures"
CASES = ROOT / "procedure"

pytestmark = [
    pytest.mark.golden,
    pytest.mark.skipif(not CASES.is_dir(), reason="local_fixtures/procedure is not present"),
]


def _cases() -> list[Path]:
    return sorted(p for p in CASES.iterdir() if p.is_dir()) if CASES.is_dir() else []


@pytest.mark.parametrize("case", _cases(), ids=lambda p: p.name)
def test_procedure_case_metrics(case: Path) -> None:
    reference_path = next(case.glob("reference.*"))
    reference = prepare_reference(decode(reference_path.read_bytes()).rgb)
    rows = []
    for path in sorted(p for p in case.iterdir() if p.is_file() and p != reference_path):
        try:
            photo = decode(path.read_bytes())
        except UnsupportedFormatError:
            continue
        result = compute_edit(photo.rgb, reference).align
        rows.append(
            {
                "photo": path.name,
                "status": result.status,
                "confidence": round(result.confidence, 4),
                "inliers": result.inliers,
                "ecc_cc": round(result.ecc_cc, 4),
                "model": result.model,
                "homography_note": result.homography_note or "",
                "tight_crop": result.tight_crop,
                "mirrored": result.mirrored,
                "detail_ratio": round(result.detail_ratio, 3),
            }
        )
    report = ROOT / "reports" / f"procedure_{case.name}.csv"
    report.parent.mkdir(parents=True, exist_ok=True)
    with report.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["photo"])
        writer.writeheader()
        writer.writerows(rows)
    assert rows, f"no photos found in {case}"
