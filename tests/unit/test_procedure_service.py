import io

import numpy as np
import pytest
from PIL import ExifTags, Image

from dental_image_organizer.core.align import prepare_reference
from dental_image_organizer.core.color import to_lab
from dental_image_organizer.core.imageio import decode
from dental_image_organizer.core.render import coverage_mask
from dental_image_organizer.services.procedure import (
    compute_edit,
    output_size,
    render_edit,
    render_reference,
)
from support.scenarios import about_center, make_pair
from support.synthetic import grade, make_scene

W, H = 2400, 1600


@pytest.fixture(scope="module")
def scene() -> np.ndarray:
    return make_scene(51, W, H)


def _mean_delta_e(a: np.ndarray, b: np.ndarray, mask: np.ndarray) -> float:
    return float(np.linalg.norm(to_lab(a) - to_lab(b), axis=-1)[mask].mean())


def test_processed_photo_matches_reference_geometry_and_color(scene: np.ndarray) -> None:
    pair = make_pair(scene, about_center(W, H, 0.9, 6.0))
    # Another session: darker and cooler than the graded reference.
    photo = grade(pair.photo, gain=0.85, warm=-8.0)
    reference = prepare_reference(pair.reference)

    edit = compute_edit(photo, reference)
    size = (pair.reference.shape[1], pair.reference.shape[0])
    out = render_edit(photo, edit, size)

    covered = coverage_mask((W, H), edit.transform, size)
    assert edit.align.status == "ok"
    assert edit.curves is not None
    assert _mean_delta_e(out, pair.reference, covered) < 4


def test_fallback_keeps_the_photo_colors(scene: np.ndarray) -> None:
    reference = prepare_reference(grade(scene[400:1200, 600:1800]))

    edit = compute_edit(make_scene(52, W, H), reference)

    assert edit.align.status == "needs_review"
    assert edit.curves is None


def test_exif_rotated_portrait_photo_is_aligned(scene: np.ndarray) -> None:
    pair = make_pair(scene, about_center(W, H, 1.0, 3.0))
    # Store the photo the way an iPhone does in portrait: pixels rotated, orientation tag 6.
    stored = Image.fromarray(pair.photo).transpose(Image.Transpose.ROTATE_90)
    exif = Image.Exif()
    exif[ExifTags.Base.Orientation] = 6
    buf = io.BytesIO()
    stored.save(buf, format="JPEG", quality=95, exif=exif.tobytes())

    photo = decode(buf.getvalue()).rgb
    edit = compute_edit(photo, prepare_reference(pair.reference))

    assert photo.shape == pair.photo.shape
    assert edit.align.status == "ok"


@pytest.mark.parametrize(
    ("scale", "expected"), [(1.0, (1200, 800)), (1.5, (1800, 1200)), (2.0, (2400, 1600))]
)
def test_output_size(scale: float, expected: tuple[int, int]) -> None:
    assert output_size((1200, 800), scale) == expected


def test_output_scale_outside_the_allowed_set_is_rejected() -> None:
    with pytest.raises(ValueError):
        output_size((1200, 800), 3.0)


def test_render_reference_upscales_the_reference() -> None:
    reference = make_scene(53, 600, 400)

    assert render_reference(reference, (900, 600)).shape == (600, 900, 3)
    assert render_reference(reference, (600, 400)) is reference
