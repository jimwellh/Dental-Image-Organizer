"""Cases shared by `core.transform` and the frontend's `transform.ts` (spec §8.1).

Python is the reference: the frontend test loads `cases.json` and must reproduce every
`expected` value. Regenerate with `uv run python tests/support/transform_fixture.py`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from dental_image_organizer.core import transform as tf

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "transform" / "cases.json"

SIMILARITIES = [  # scale, angle_deg, tx, ty, mirror
    (1.0, 0.0, 0.0, 0.0, False),
    (1.3, 12.0, 0.05, -0.02, False),
    (0.7, -30.0, 0.2, 0.1, True),
]


def _m(matrix: npt.NDArray[np.float64]) -> list[float]:
    return [float(v) for v in matrix.reshape(-1)]


def _p(points: npt.ArrayLike) -> list[list[float]]:
    return [[float(x), float(y)] for x, y in np.asarray(points, dtype=np.float64).reshape(-1, 2)]


def _fit(src: npt.ArrayLike, dst: npt.ArrayLike) -> dict[str, Any]:
    return {"src": _p(src), "dst": _p(dst), "expected": _m(tf.fit_similarity(src, dst))}


def _dlt(src: list[list[float]], dst: list[list[float]]) -> npt.NDArray[np.float64]:
    """Solve the 8 x 8 system of four correspondences, with h[8] fixed to 1."""
    s = np.asarray(src, dtype=np.float64)
    d = np.asarray(dst, dtype=np.float64)
    a = np.zeros((8, 8))
    b = np.zeros(8)
    for i, ((x, y), (u, v)) in enumerate(zip(s, d, strict=True)):
        a[2 * i] = [x, y, 1.0, 0.0, 0.0, 0.0, -u * x, -u * y]
        a[2 * i + 1] = [0.0, 0.0, 0.0, x, y, 1.0, -v * x, -v * y]
        b[2 * i], b[2 * i + 1] = u, v
    return np.append(np.linalg.solve(a, b), 1.0)


def build_fixture() -> dict[str, Any]:
    rng = np.random.default_rng(8)
    sims = [tf.similarity(s, a, x, y, mirror=m) for s, a, x, y, m in SIMILARITIES]
    tilt = np.array([[1.0, 0.05, 0.0], [0.02, 1.0, 0.0], [0.08, -0.05, 1.0]])
    mats = sims + [tf.normalize(s @ tilt) for s in sims]
    points = rng.uniform(0.0, 1.0, size=(5, 2))
    noisy_src = rng.uniform(0.0, 1.0, size=(6, 2))
    known = tf.similarity(1.1, 7.0, 0.03, 0.01)
    mirrored = tf.similarity(0.9, 20.0, 0.4, 0.1, mirror=True)
    quads = [  # photo corners -> output corners, as clicked in the four-point mode
        (
            [[0.1, 0.1], [0.9, 0.12], [0.88, 0.7], [0.12, 0.68]],
            [[0, 0], [1, 0], [1, 0.75], [0, 0.75]],
        ),
        (
            [[0.2, 0.15], [0.7, 0.1], [0.75, 0.6], [0.15, 0.55]],
            [[0.1, 0.1], [0.9, 0.1], [0.9, 0.6], [0.1, 0.6]],
        ),
    ]
    return {
        "multiply": [
            {"a": _m(a), "b": _m(b), "expected": _m(a @ b)} for a in mats[:3] for b in mats[3:]
        ],
        "invert": [{"h": _m(h), "expected": _m(tf.invert(h))} for h in mats],
        "map_points": [
            {"h": _m(h), "points": _p(points), "expected": _p(tf.map_points(h, points))}
            for h in mats
        ],
        "similarity": [
            {"scale": s, "angle_deg": a, "tx": x, "ty": y, "mirror": m, "expected": _m(h)}
            for (s, a, x, y, m), h in zip(SIMILARITIES, sims, strict=True)
        ],
        "fit_similarity": [
            _fit(points[:2], tf.map_points(known, points[:2])),  # two-point mode
            _fit(noisy_src, tf.map_points(known, noisy_src) + rng.normal(0, 0.002, (6, 2))),
            _fit(points, tf.map_points(mirrored, points)),  # a mirror fits better
        ],
        "homography_from_points": [
            {"src": s, "dst": d, "expected": _m(_dlt(s, d))} for s, d in quads
        ],
        "closest_similarity": [
            {
                "h": _m(h),
                "frame": [1.0, 0.75],
                "expected": _m(tf.closest_similarity(h, 1.0, 0.75)),
            }
            for h in mats[3:]
        ],
        "to_pixel": [
            {
                "h": _m(h),
                "in_width": 4032.0,
                "out_width": 1600.0,
                "points": _p(points * 4032),
                "expected_points": _p(tf.map_points(tf.to_pixel(h, 4032.0, 1600.0), points * 4032)),
            }
            for h in (mats[1], mats[4])
        ],
    }


def main() -> None:
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    # Bytes, not text mode: the file must keep LF endings on Windows.
    text = json.dumps(build_fixture(), indent=2, sort_keys=True) + "\n"
    FIXTURE.write_bytes(text.encode("utf-8"))


if __name__ == "__main__":
    main()
