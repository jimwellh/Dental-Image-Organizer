"""Synthetic intraoral views for the nine composite slots (spec §8.1).

Each view has the layout its leveling rule reads: two arches meeting at a horizontal boundary
(occlusion views), a symmetric U-shaped arch (occlusal views), or one row of teeth with a
straight occlusal edge (buccal views). Left views mirror right views, as in real photos, and
side views carry a perspective size gradient so they are not symmetric.
"""

from __future__ import annotations

import cv2
import numpy as np

from support.synthetic import ENAMEL_RGB, GUM_RGB, _smooth_noise

VIEW_SIZE = (1800, 1200)
PALATE_RGB = np.array([170, 80, 95], np.float32)
DARK_RGB = np.array([60, 25, 30], np.float32)
METAL_RGB = np.array([150, 150, 155], np.float32)


def make_view(
    slot: str, seed: int, *, angle: float = 0.0, size: tuple[int, int] = VIEW_SIZE
) -> np.ndarray:
    """An upright view for `slot`, then rotated by `angle` degrees (tf.similarity convention)."""
    w, h = size
    rng = np.random.default_rng(seed)
    field = sum(a * _smooth_noise(rng, h, w, c) for c, a in ((200, 0.5), (40, 0.3), (8, 0.2)))
    field = (field - field.mean()) / (field.std() + 1e-6)
    img = GUM_RGB + field[..., None] * np.array([16, 10, 10], np.float32)
    teeth = np.zeros((h, w), np.uint8)

    def jitter(spread: float) -> float:
        return float(rng.uniform(-spread, spread))

    if slot.startswith("occlusion"):
        _boundary_rows(teeth, w, h, 0.0 if slot == "occlusion_front" else 0.45, jitter)
        if slot != "occlusion_front":
            img[:, : int(0.12 * w)] = METAL_RGB  # retractor at the far side
        cv2.line(teeth, (0, h // 2), (w, h // 2), 0, max(3, h // 150))
    elif slot.endswith("occlusal"):
        _arch(img, teeth, w, h, jitter)
    else:
        _buccal_row(img, teeth, w, h, jitter)
        if slot.startswith("lower"):
            img, teeth = img[::-1].copy(), teeth[::-1].copy()

    enamel = ENAMEL_RGB + field[..., None] * np.array([9, 9, 11], np.float32)
    img[teeth > 0] = enamel[teeth > 0]
    for _ in range(400):
        # Draw order (center, radius, color) is part of the seeded scene; keep it.
        center = (int(rng.integers(0, w)), int(rng.integers(0, h)))
        radius = int(rng.integers(2, 5))
        color = tuple(float(c) for c in rng.integers(40, 110, 3))
        cv2.circle(img, center, radius, color, -1)
    if slot.endswith("_left"):
        img = img[:, ::-1].copy()
    out = np.clip(img, 0, 255).astype(np.uint8)
    if angle:
        m = cv2.getRotationMatrix2D((w / 2, h / 2), -angle, 1.0)
        out = cv2.warpAffine(
            out, m, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE
        )
    return out


def _boundary_rows(teeth, w, h, gradient, jitter) -> None:
    n = 8
    x = 0.1 * w
    widths = np.linspace(1 + gradient, 1 - gradient, n)
    widths = widths / widths.sum() * 0.8 * w
    for i in range(n):
        x0, x1 = int(x), int(x + widths[i] - 0.01 * w)
        top = int(h * (0.27 + 0.03 * abs(i - (n - 1) / 2) / n) + jitter(8))
        bot = int(h * (0.73 - 0.03 * abs(i - (n - 1) / 2) / n) + jitter(8))
        cv2.rectangle(teeth, (x0, top), (x1, h // 2), 255, -1)
        cv2.rectangle(teeth, (x0 + int(0.01 * w), h // 2), (x1 - int(0.01 * w), bot), 255, -1)
        x += widths[i]


def _arch(img, teeth, w, h, jitter) -> None:
    cx, top, a, b = w / 2, 0.15 * h, 0.33 * w, 0.62 * h
    pts = np.array(
        [[cx + a * np.cos(t), top + b * (1 - np.sin(t))] for t in np.linspace(0, np.pi, 200)],
        np.int32,
    )
    cv2.fillPoly(img, [pts], PALATE_RGB.tolist())
    for t in np.linspace(0.08, np.pi - 0.08, 14):
        x, y = cx + a * np.cos(t), top + b * (1 - np.sin(t))
        r = 0.035 * w * (1.6 - 0.9 * np.sin(t))  # molars large, incisors small
        cv2.circle(teeth, (int(x + jitter(4)), int(y + jitter(4))), int(r), 255, -1)


def _buccal_row(img, teeth, w, h, jitter) -> None:
    edge = int(0.56 * h)
    img[edge:] = DARK_RGB
    img[int(0.85 * h) :, : int(0.25 * w)] = METAL_RGB
    widths = np.linspace(1.5, 0.7, 7)
    widths = widths / widths.sum() * 0.78 * w
    x = 0.12 * w
    for i in range(7):
        top = int(h * 0.30 + jitter(25))
        cv2.rectangle(teeth, (int(x), top), (int(x + widths[i] - 0.012 * w), edge), 255, -1)
        x += widths[i]
