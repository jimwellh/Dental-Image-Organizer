from __future__ import annotations

import json
from typing import Any

import cv2
import numpy as np

from support.transform_fixture import FIXTURE, build_fixture

TOLERANCE = 1e-9


def _assert_close(actual: Any, expected: Any, path: str = "$") -> None:
    # Plain == would fail on the last bit across operating systems and BLAS builds.
    if isinstance(expected, dict):
        assert isinstance(actual, dict), path
        assert actual.keys() == expected.keys(), path
        for key in expected:
            _assert_close(actual[key], expected[key], f"{path}.{key}")
    elif isinstance(expected, list):
        assert isinstance(actual, list), path
        assert len(actual) == len(expected), path
        for i, (a, e) in enumerate(zip(actual, expected, strict=True)):
            _assert_close(a, e, f"{path}[{i}]")
    elif isinstance(expected, bool | str):
        assert actual == expected, path
    else:
        assert abs(actual - expected) <= TOLERANCE * max(1.0, abs(expected)), path


def test_the_shared_fixture_is_current() -> None:
    _assert_close(json.loads(FIXTURE.read_bytes()), build_fixture())


def test_the_four_point_reference_matches_opencv() -> None:
    cases = build_fixture()["homography_from_points"]
    assert cases
    for case in cases:
        src = np.array(case["src"], dtype=np.float32)
        dst = np.array(case["dst"], dtype=np.float32)
        reference = cv2.getPerspectiveTransform(src, dst)
        reference = reference / reference[2, 2]
        np.testing.assert_allclose(
            np.array(case["expected"]).reshape(3, 3), reference, atol=1e-5, rtol=0
        )
