"""Cached photo, sequence, and composite renders (spec §4.8)."""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from dental_image_organizer.core.imageio import RGBImage
from dental_image_organizer.errors import NotReadyError
from dental_image_organizer.services import composite_case, originals, procedure_case, renders
from dental_image_organizer.services.context import AppContext
from dental_image_organizer.services.intake import add_photos
from support.app import ProcedureSet, jpeg, make_context, procedure_set, upload, view_set, wait
from support.synthetic import make_scene

Files = list[tuple[str, bytes]]


@pytest.fixture(scope="module")
def photos() -> ProcedureSet:
    return procedure_set()


@pytest.fixture(scope="module")
def ten() -> Files:
    return view_set(count=10)


def _size(data: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(data)) as image:
        return image.size


def _pixels(data: bytes) -> RGBImage:
    with Image.open(io.BytesIO(data)) as image:
        return np.asarray(image.convert("RGB"))


def _procedure(ctx: AppContext, photos: ProcedureSet) -> tuple[str, str, str]:
    """A case with the reference and two aligned photos; returns case, reference, and photo ids."""
    library = ctx.library()
    patient = library.create_patient("Alex Wang", None)
    case_id = library.create_case(patient.id, "procedure", "implant 16").id
    outcome = procedure_case.set_reference(ctx, case_id, upload("ref.jpg", photos.reference))
    assert outcome.job_id is not None
    wait(ctx, outcome.job_id)
    outcome = procedure_case.upload(
        ctx,
        case_id,
        [upload("IMG_0001.jpg", photos.intra), upload("IMG_0002.jpg", photos.post)],
    )
    assert outcome.job_id is not None
    wait(ctx, outcome.job_id)
    case = ctx.library().read_case(case_id)
    reference = case.reference()
    assert reference is not None
    first = next(p for p in case.photos if p.role == "photo")
    return case_id, reference.id, first.id


def _composite_case(ctx: AppContext) -> str:
    library = ctx.library()
    patient = library.create_patient("Alex Wang", None)
    block = composite_case.composite_block(ctx.settings.get())
    return library.create_case(patient.id, "composite", "full mouth", composite=block).id


def _composite(ctx: AppContext, files: Files) -> str:
    case_id = _composite_case(ctx)
    outcome = composite_case.upload(ctx, case_id, [upload(n, d) for n, d in files])
    assert outcome.job_id is not None
    wait(ctx, outcome.job_id)
    return case_id


def test_original_variants(ctx: AppContext) -> None:
    case_id = _composite_case(ctx)
    data = jpeg(make_scene(5, 1800, 1200), "2026:10:16 10:00:00")
    added, _ = add_photos(ctx, case_id, [upload("IMG_0001.jpg", data)], role="member")

    sizes = {
        size: _size(renders.photo_image(ctx, case_id, added[0], variant="original", size=size))
        for size in ("thumb", "preview", "full")
    }

    assert sizes == {"thumb": (320, 213), "preview": (1600, 1067), "full": (1800, 1200)}


def test_processed_variants_follow_the_edit(ctx: AppContext, photos: ProcedureSet) -> None:
    case_id, _, photo_id = _procedure(ctx, photos)

    sizes = {
        size: _size(renders.photo_image(ctx, case_id, photo_id, variant="processed", size=size))
        for size in ("thumb", "preview", "full")
    }

    assert sizes == {"thumb": (320, 213), "preview": (1200, 800), "full": (1200, 800)}


def test_renders_are_cached(
    ctx: AppContext, photos: ProcedureSet, monkeypatch: pytest.MonkeyPatch
) -> None:
    case_id, _, photo_id = _procedure(ctx, photos)
    first = renders.photo_image(ctx, case_id, photo_id, variant="processed", size="full")
    decodes: list[int] = []
    real = originals.decode

    def spy(data: bytes):
        decodes.append(1)
        return real(data)

    monkeypatch.setattr(originals, "decode", spy)

    second = renders.photo_image(ctx, case_id, photo_id, variant="processed", size="full")

    assert second == first
    assert decodes == []


def test_a_changed_edit_renders_anew(ctx: AppContext, photos: ProcedureSet) -> None:
    case_id, _, photo_id = _procedure(ctx, photos)
    before = renders.photo_image(ctx, case_id, photo_id, variant="processed", size="full")
    assert _size(before) == (1200, 800)

    procedure_case.set_output_scale(ctx, case_id, 2.0)

    after = renders.photo_image(ctx, case_id, photo_id, variant="processed", size="full")
    assert _size(after) == (2400, 1600)


def test_unprocessed_photos_are_not_ready(ctx: AppContext, ten: Files) -> None:
    case_id = _composite(ctx, ten)
    case = ctx.library().read_case(case_id)
    tray = next(p for p in case.photos if p.slot is None)
    failed = case.photos[0]
    ctx.library().update_case(
        case_id,
        lambda c: c.replace_photo(failed.model_copy(update={"state": "failed", "error": "bad"})),
    )
    data = jpeg(make_scene(6, 1800, 1200), "2026:10:16 10:05:00")
    added, _ = add_photos(ctx, case_id, [upload("late.jpg", data)], role="member")

    for photo_id in (tray.id, failed.id, added[0]):
        with pytest.raises(NotReadyError):
            renders.photo_image(ctx, case_id, photo_id, variant="processed", size="thumb")
    assert renders.photo_image(ctx, case_id, added[0], variant="original", size="thumb")


def test_the_reference_has_no_black_edge(ctx: AppContext, photos: ProcedureSet) -> None:
    case_id, reference_id, _ = _procedure(ctx, photos)
    procedure_case.set_output_scale(ctx, case_id, 1.5)

    full = renders.photo_image(ctx, case_id, reference_id, variant="processed", size="full")

    pixels = _pixels(full)
    assert pixels.shape[:2] == (1200, 1800)
    assert pixels[:, -1].any()
    assert pixels[-1].any()


def test_composite_preview_and_full(ctx: AppContext, ten: Files) -> None:
    case_id = _composite(ctx, ten)

    full = renders.composite_image(ctx, case_id, size="full")
    preview = renders.composite_image(ctx, case_id, size="preview")

    assert _size(full) == (3600, 2400)
    assert _size(preview) == (1600, 1067)


def test_composite_gap_widens_the_grid(tmp_path: Path, ten: Files) -> None:
    ctx = make_context(tmp_path, gap_px=10)
    try:
        case_id = _composite(ctx, ten)
        assert _size(renders.composite_image(ctx, case_id, size="full")) == (3620, 2420)
    finally:
        ctx.close()


def test_sequence_preview_and_full(ctx: AppContext, photos: ProcedureSet) -> None:
    case_id, _, _ = _procedure(ctx, photos)

    full = renders.sequence_image(ctx, case_id, size="full")
    preview = renders.sequence_image(ctx, case_id, size="preview")

    assert _size(full) == (3600, 800)
    assert _size(preview)[0] <= 1600


def test_empty_cases_are_not_ready(ctx: AppContext) -> None:
    library = ctx.library()
    patient = library.create_patient("Alex Wang", None)
    procedure = library.create_case(patient.id, "procedure", "implant 16")
    composite_id = _composite_case(ctx)

    with pytest.raises(NotReadyError):
        renders.sequence_image(ctx, procedure.id, size="full")
    with pytest.raises(NotReadyError):
        renders.composite_image(ctx, composite_id, size="full")


def test_fit_long_edge_never_upscales() -> None:
    assert renders.fit_long_edge((1200, 800), 1600) == (1200, 800)
    assert renders.fit_long_edge((1800, 1200), 320) == (320, 213)
