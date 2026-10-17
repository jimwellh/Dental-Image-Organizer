"""Color matching to the reference (spec §4.5.2): monotone per-channel curves in CIE Lab."""

from __future__ import annotations

from typing import cast

import cv2
import numpy as np
import numpy.typing as npt

from dental_image_organizer.core.imageio import RGBImage

LabImage = npt.NDArray[np.float32]


def to_lab(rgb: RGBImage) -> LabImage:
    """sRGB uint8 to Lab float32 with L in [0, 100] and a*, b* in their natural units."""
    return cast(LabImage, cv2.cvtColor(rgb.astype(np.float32) / 255.0, cv2.COLOR_RGB2LAB))


def from_lab(lab: LabImage) -> RGBImage:
    rgb = cv2.cvtColor(lab.astype(np.float32), cv2.COLOR_LAB2RGB)
    return np.clip(np.rint(rgb * 255.0), 0, 255).astype(np.uint8)
