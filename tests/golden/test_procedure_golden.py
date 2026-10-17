"""Alignment metrics on real, de-identified photos (spec §8.3). Never runs in CI.

Layout: local_fixtures/procedure/<case>/reference.<ext> plus that case's other photos.
Each run writes local_fixtures/reports/procedure_<case>.csv for threshold tuning.
"""

import csv
from pathlib import Path

import numpy as np
import pytest

from dental_image_organizer.core.align import (
    WORKING_LONG_EDGE,
    PreparedReference,
    prepare_reference,
)
from dental_image_organizer.core.color import to_lab
from dental_image_organizer.core.imageio import decode, resize_long_edge
from dental_image_organizer.core.render import coverage_mask, render
from dental_image_organizer.errors import UnsupportedFormatError
from dental_image_organizer.services.procedure import ProcedureEdit, compute_edit, render_edit

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
        edit = compute_edit(photo.rgb, reference)
        result = edit.align
        before, after = _color_error(photo.rgb, edit, reference)
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
                "curves_fitted": edit.curves is not None,
                "delta_e_before": round(before, 2),
                "delta_e_after": round(after, 2),
            }
        )
    report = ROOT / "reports" / f"procedure_{case.name}.csv"
    report.parent.mkdir(parents=True, exist_ok=True)
    with report.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["photo"])
        writer.writeheader()
        writer.writerows(rows)
    assert rows, f"no photos found in {case}"


def _color_error(
    photo_rgb: np.ndarray, edit: ProcedureEdit, reference: PreparedReference
) -> tuple[float, float]:
    """Mean Delta E76 to the reference working copy over the covered overlap, before and after
    color matching."""
    working, _ = resize_long_edge(photo_rgb, WORKING_LONG_EDGE)
    size = (reference.working.shape[1], reference.working.shape[0])
    valid = coverage_mask((working.shape[1], working.shape[0]), edit.transform, size)
    if not valid.any():
        return float("nan"), float("nan")
    target = to_lab(reference.working)

    def error(image: np.ndarray) -> float:
        return float(np.linalg.norm(to_lab(image) - target, axis=-1)[valid].mean())

    return error(render(working, edit.transform, size)), error(render_edit(working, edit, size))
