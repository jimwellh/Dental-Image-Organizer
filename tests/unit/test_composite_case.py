"""Composite cases in the library: slots, normalization, and cell edits (spec §3.3, §4.6, §4.7)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import structlog
from structlog.testing import capture_logs

from dental_image_organizer.core import transform as tf
from dental_image_organizer.core.slots import DEFAULT_FLIPS, SLOTS, Slot
from dental_image_organizer.errors import InvalidEditError, InvalidRequestError, NotReadyError
from dental_image_organizer.services import composite_case
from dental_image_organizer.services.context import AppContext
from dental_image_organizer.services.intake import add_photos
from dental_image_organizer.storage.schema import CaseRecord, EditRecord, PhotoRecord, new_id
from support.app import make_context, upload, view_set, wait
from support.embeddings import synthetic_pairs
from support.fake_model import install_fake_model

CELL = (1200, 800)
ROW_MAJOR: dict[str, Slot | None] = {f"IMG_{i + 1:04d}.jpg": slot for i, slot in enumerate(SLOTS)}
NOT_SIMILAR = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.001, 0.0, 1.0]

Files = list[tuple[str, bytes]]


@pytest.fixture(scope="module")
def nine() -> Files:
    return view_set()


@pytest.fixture(scope="module")
def ten() -> Files:
    return view_set(count=10)


def _case(ctx: AppContext) -> str:
    library = ctx.library()
    patient = library.create_patient("Alex Wang", None)
    block = composite_case.composite_block(ctx.settings.get())
    return library.create_case(patient.id, "composite", "full mouth", composite=block).id


def _upload(ctx: AppContext, case_id: str, files: Files) -> dict[str, Any]:
    """The result of the processing job."""
    outcome = composite_case.upload(ctx, case_id, [upload(name, data) for name, data in files])
    assert outcome.job_id is not None
    return dict(wait(ctx, outcome.job_id).result or {})


def _read(ctx: AppContext, case_id: str) -> CaseRecord:
    return ctx.library().read_case(case_id)


def _named(case: CaseRecord) -> dict[str, PhotoRecord]:
    return {p.source_name: p for p in case.photos}


def _slots(case: CaseRecord) -> dict[str, Slot | None]:
    return {p.source_name: p.slot for p in case.photos}


def _cell(case: CaseRecord, slot: Slot) -> PhotoRecord:
    return next(p for p in case.photos if p.slot == slot)


def _edit(photo: PhotoRecord) -> EditRecord:
    assert photo.edit is not None
    return photo.edit


def _placement(case: CaseRecord) -> dict[Slot, str]:
    return {p.slot: p.id for p in case.photos if p.slot is not None}


def _source(case: CaseRecord) -> str:
    assert case.composite is not None
    return case.composite.slots_source


@pytest.fixture(scope="module")
def shared(
    tmp_path_factory: pytest.TempPathFactory, ten: Files
) -> Iterator[tuple[AppContext, str]]:
    """One case of ten photos for the tests that must leave it unchanged."""
    ctx = make_context(tmp_path_factory.mktemp("shared"))
    try:
        case_id = _case(ctx)
        _upload(ctx, case_id, ten)
        yield ctx, case_id
    finally:
        ctx.close()


def test_upload_assigns_by_shooting_order_and_plans_every_cell(
    ctx: AppContext, nine: Files
) -> None:
    case_id = _case(ctx)

    result = _upload(ctx, case_id, nine)

    case = _read(ctx, case_id)
    assert _slots(case) == ROW_MAJOR
    for photo in case.photos:
        assert photo.slot is not None and photo.state == "ready"
        edit = _edit(photo)
        assert edit.output_size == CELL
        assert edit.flip == DEFAULT_FLIPS[photo.slot]
        assert (edit.model, edit.source, edit.status) == ("similarity", "auto", "ok")
        assert edit.algorithm_version == composite_case.COMPOSITE_ALGORITHM
        method = "identity" if photo.slot == "occlusion_front" else "lab_reinhard_v1"
        assert edit.color.method == method
    assert result["assignment"] == {"method": "order", "reason": "untrained"}
    assert [(p["state"], p["status"]) for p in result["photos"]] == [("ready", "ok")] * 9
    assert _source(case) == "auto"


def test_the_settings_shooting_order_is_used(tmp_path: Path, nine: Files) -> None:
    ctx = make_context(tmp_path, shooting_order=tuple(reversed(SLOTS)))
    try:
        case_id = _case(ctx)

        _upload(ctx, case_id, nine)

        assert _named(_read(ctx, case_id))["IMG_0001.jpg"].slot == "lower_left"
    finally:
        ctx.close()


def test_extra_photos_go_to_the_tray(shared: tuple[AppContext, str]) -> None:
    ctx, case_id = shared

    case = _read(ctx, case_id)

    tray = [p for p in case.photos if p.slot is None]
    assert [(p.source_name, p.state, p.edit) for p in tray] == [("IMG_0010.jpg", "ready", None)]
    assert sum(p.edit is not None for p in case.photos) == 9


def test_an_unreadable_photo_keeps_its_rank(ctx: AppContext, nine: Files) -> None:
    case_id = _case(ctx)
    files = [(name, data[:300] if name == "IMG_0005.jpg" else data) for name, data in nine]

    result = _upload(ctx, case_id, files)

    case = _read(ctx, case_id)
    broken = _named(case)["IMG_0005.jpg"]
    assert (broken.state, broken.slot, broken.edit) == ("failed", None, None)
    assert broken.error
    assert all(p.slot != "occlusion_front" for p in case.photos)
    assert _named(case)["IMG_0006.jpg"].slot == "occlusion_left"
    assert _slots(case) == ROW_MAJOR | {"IMG_0005.jpg": None}
    # The default baseline is empty, so the first filled cell takes its place.
    assert _edit(_cell(case, "upper_right")).color.method == "identity"
    outcomes = {p["photo_id"]: p for p in result["photos"]}
    assert outcomes[broken.id] == {
        "photo_id": broken.id,
        "state": "failed",
        "status": None,
        "error": broken.error,
    }


def test_swapped_slots_are_kept_when_photos_are_added(ctx: AppContext, ten: Files) -> None:
    case_id = _case(ctx)
    _upload(ctx, case_id, ten[:9])
    slots = _placement(_read(ctx, case_id))
    slots["upper_right"], slots["upper_left"] = slots["upper_left"], slots["upper_right"]

    swapped = composite_case.set_slots(ctx, case_id, slots)

    named = _named(swapped)
    assert (named["IMG_0001.jpg"].slot, named["IMG_0003.jpg"].slot) == ("upper_left", "upper_right")
    assert _source(swapped) == "manual"
    assert _edit(named["IMG_0001.jpg"]).flip == DEFAULT_FLIPS["upper_left"]
    assert _edit(named["IMG_0003.jpg"]).flip == DEFAULT_FLIPS["upper_right"]
    assert _read(ctx, case_id) == swapped

    result = _upload(ctx, case_id, ten[9:])

    case = _read(ctx, case_id)
    assert result["assignment"] is None
    assert _slots(case) == _slots(swapped) | {"IMG_0010.jpg": None}
    assert _named(case)["IMG_0010.jpg"].edit is None

    job = wait(ctx, composite_case.start_auto_assign(ctx, case_id))

    case = _read(ctx, case_id)
    assert job.kind == "assign"
    assert job.result == {"assignment": {"method": "order", "reason": "untrained"}}
    assert _slots(case) == ROW_MAJOR | {"IMG_0010.jpg": None}
    assert _source(case) == "auto"


def test_moving_a_photo_swaps_it_with_the_slot_holder(ctx: AppContext, nine: Files) -> None:
    case_id = _case(ctx)
    _upload(ctx, case_id, nine)

    moved = composite_case.move_photo(
        ctx, case_id, _named(_read(ctx, case_id))["IMG_0001.jpg"].id, "upper_occlusal"
    )

    named = _named(moved)
    assert (named["IMG_0001.jpg"].slot, named["IMG_0002.jpg"].slot) == (
        "upper_occlusal",
        "upper_right",
    )
    assert _edit(named["IMG_0001.jpg"]).flip == DEFAULT_FLIPS["upper_occlusal"]
    assert _edit(named["IMG_0002.jpg"]).flip == DEFAULT_FLIPS["upper_right"]
    assert _source(moved) == "manual"

    trayed = composite_case.move_photo(ctx, case_id, named["IMG_0002.jpg"].id, None)

    gone = trayed.photo(named["IMG_0002.jpg"].id)
    assert (gone.slot, gone.edit) == (None, None)
    assert "upper_right" not in _placement(trayed)
    assert _read(ctx, case_id) == trayed


def test_a_flip_change_recomputes_the_cell(ctx: AppContext, nine: Files) -> None:
    case_id = _case(ctx)
    _upload(ctx, case_id, nine)
    before = _cell(_read(ctx, case_id), "upper_right")

    case = composite_case.set_flip(ctx, case_id, before.id, "none")

    edit = _edit(case.photo(before.id))
    assert (edit.flip, edit.source) == ("none", "auto")
    assert edit.transform != _edit(before).transform
    assert case.photo(before.id).slot == "upper_right"
    assert _read(ctx, case_id) == case


def test_a_manual_cell_is_kept_by_replans(ctx: AppContext, nine: Files) -> None:
    case_id = _case(ctx)
    _upload(ctx, case_id, nine)
    cell = _cell(_read(ctx, case_id), "upper_left")
    auto = _edit(cell)
    zoomed = tf.scaling(1.1) @ auto.matrix()

    edited = composite_case.apply_manual_edit(
        ctx, case_id, cell.id, [float(v) for v in zoomed.ravel()]
    )

    manual = _edit(edited.photo(cell.id))
    assert (manual.source, manual.flip, manual.output_size) == ("manual", auto.flip, CELL)
    assert np.allclose(manual.transform, zoomed.ravel())

    slots = _placement(edited)
    slots["lower_right"], slots["lower_left"] = slots["lower_left"], slots["lower_right"]
    swapped = composite_case.set_slots(ctx, case_id, slots)

    kept = _edit(swapped.photo(cell.id))
    assert (kept.source, kept.transform) == ("manual", manual.transform)

    reset = composite_case.reset_edit(ctx, case_id, cell.id)

    edit = _edit(reset.photo(cell.id))
    assert (edit.source, edit.flip) == ("auto", DEFAULT_FLIPS["upper_left"])
    assert np.allclose(edit.transform, auto.transform)


def test_the_baseline_photo_gets_identity_color(ctx: AppContext, nine: Files) -> None:
    case_id = _case(ctx)
    _upload(ctx, case_id, nine)
    case = _read(ctx, case_id)
    lower_left = _cell(case, "lower_left")
    assert case.composite is not None
    layout = case.composite.model_copy(
        update={
            "baseline_photo_id": lower_left.id,
            "cell_size": (900, 600),
            "gap_px": 10,
            "gap_color": "#FFFFFF",
            "slots_source": "manual",
        }
    )

    changed = composite_case.set_layout(ctx, case_id, layout)

    assert _edit(changed.photo(lower_left.id)).color.method == "identity"
    assert _edit(_cell(changed, "occlusion_front")).color.method == "lab_reinhard_v1"
    assert {_edit(p).output_size for p in changed.photos} == {(900, 600)}
    assert changed.composite == layout.model_copy(update={"slots_source": "auto"})
    assert _read(ctx, case_id) == changed


def test_learned_flips_are_the_defaults(ctx: AppContext, nine: Files) -> None:
    for _ in range(3):
        ctx.ai.record_flips(uuid.uuid4().hex, {"upper_right": "none"}, pins={})
    case_id = _case(ctx)

    _upload(ctx, case_id, nine)

    assert composite_case.composite_flips(ctx)["upper_right"] == "none"
    assert _edit(_cell(_read(ctx, case_id), "upper_right")).flip == "none"


def test_a_trained_classifier_assigns(tmp_path: Path, nine: Files) -> None:
    ctx = make_context(tmp_path, model_spec=install_fake_model(tmp_path / "cache" / "models"))
    try:
        pairs, slots = synthetic_pairs(3, 1)
        photo_ids = [uuid.uuid4().hex for _ in slots]
        assert ctx.ai.learn(
            pairs, slots, photo_ids=photo_ids, case_id=uuid.uuid4().hex, source="teach"
        )
        case_id = _case(ctx)

        result = _upload(ctx, case_id, nine)

        assert result["assignment"] == {"method": "classifier", "reason": None}
        placed = [p for p in _read(ctx, case_id).photos if p.slot is not None]
        assert len(placed) == 9
        for photo in placed:
            edit = _edit(photo)
            assert edit.confidence is not None
            assert edit.status == ("needs_review" if edit.confidence < 0.5 else "ok")
    finally:
        ctx.close()


def test_invalid_slot_maps_are_rejected(
    shared: tuple[AppContext, str], ctx: AppContext, nine: Files
) -> None:
    shared_ctx, case_id = shared
    before = _read(shared_ctx, case_id)
    placed = _placement(before)

    for slots in (
        {"upper_right": placed["upper_right"], "upper_left": placed["upper_right"]},
        {"upper_right": new_id()},
    ):
        with pytest.raises(InvalidRequestError):
            composite_case.set_slots(shared_ctx, case_id, slots)

    assert _read(shared_ctx, case_id) == before
    # Recorded, but not processed: no job is submitted for it.
    pending_case = _case(ctx)
    (pending,), _ = add_photos(ctx, pending_case, [upload(*nine[0])], role="member")
    with pytest.raises(InvalidRequestError):
        composite_case.set_slots(ctx, pending_case, {"upper_right": pending})
    with pytest.raises(InvalidRequestError):
        composite_case.move_photo(ctx, pending_case, pending, "upper_right")
    assert _read(ctx, pending_case).photo(pending).slot is None


def test_manual_cell_edits_must_be_similarities(shared: tuple[AppContext, str]) -> None:
    ctx, case_id = shared
    before = _read(ctx, case_id)

    with pytest.raises(InvalidEditError):
        composite_case.apply_manual_edit(ctx, case_id, _cell(before, "upper_right").id, NOT_SIMILAR)

    assert _read(ctx, case_id) == before


def test_cell_edits_need_a_placed_photo(shared: tuple[AppContext, str]) -> None:
    ctx, case_id = shared
    before = _read(ctx, case_id)
    tray = next(p.id for p in before.photos if p.slot is None)
    identity = [float(v) for v in tf.identity().ravel()]

    with pytest.raises(NotReadyError, match="Place the photo in a slot first"):
        composite_case.set_flip(ctx, case_id, tray, "none")
    with pytest.raises(NotReadyError, match="Place the photo in a slot first"):
        composite_case.apply_manual_edit(ctx, case_id, tray, identity)
    with pytest.raises(NotReadyError, match="Place the photo in a slot first"):
        composite_case.reset_edit(ctx, case_id, tray)

    assert _read(ctx, case_id) == before


@pytest.mark.parametrize(
    "update",
    [
        pytest.param({"baseline_photo_id": new_id()}, id="unknown-baseline"),
        pytest.param({"cell_size": (99, 800)}, id="cell-too-small"),
        pytest.param({"cell_size": (1200, 4001)}, id="cell-too-large"),
        pytest.param({"gap_px": 201}, id="gap-too-wide"),
        pytest.param({"gap_color": "black"}, id="gap-color-name"),
    ],
)
def test_invalid_layouts_are_rejected(
    shared: tuple[AppContext, str], update: dict[str, Any]
) -> None:
    ctx, case_id = shared
    before = _read(ctx, case_id)
    assert before.composite is not None

    with pytest.raises(InvalidRequestError):
        composite_case.set_layout(ctx, case_id, before.composite.model_copy(update=update))

    assert _read(ctx, case_id) == before


def test_procedure_cases_are_refused(ctx: AppContext, nine: Files) -> None:
    library = ctx.library()
    patient = library.create_patient("Alex Wang", None)
    case_id = library.create_case(patient.id, "procedure", "implant 16").id
    block = composite_case.composite_block(ctx.settings.get())
    photo_id = new_id()
    refused = [
        lambda: composite_case.upload(ctx, case_id, [upload(*nine[0])]),
        lambda: composite_case.start_auto_assign(ctx, case_id),
        lambda: composite_case.set_slots(ctx, case_id, {}),
        lambda: composite_case.move_photo(ctx, case_id, photo_id, None),
        lambda: composite_case.set_flip(ctx, case_id, photo_id, "none"),
        lambda: composite_case.apply_manual_edit(ctx, case_id, photo_id, NOT_SIMILAR),
        lambda: composite_case.reset_edit(ctx, case_id, photo_id),
        lambda: composite_case.set_layout(ctx, case_id, block),
    ]

    for call in refused:
        with pytest.raises(InvalidRequestError, match="procedure case"):
            call()

    assert library.read_case(case_id).photos == []
    assert list(library.case_dir(case_id).rglob("*.jpg")) == []


@pytest.mark.usefixtures("debug_logging")
def test_processing_logs_carry_photo_ids_never_names(ctx: AppContext, nine: Files) -> None:
    case_id = _case(ctx)
    files = [
        (f"Alex Wang {i}.jpg", data[:300] if i == 5 else data)
        for i, (_, data) in enumerate(nine, start=1)
    ]

    # capture_logs() swaps the processor chain, so contextvars are merged explicitly here.
    with capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs:
        _upload(ctx, case_id, files)

    case = _read(ctx, case_id)
    broken = _named(case)["Alex Wang 5.jpg"].id
    levels = {entry["slot"]: entry["photo_id"] for entry in logs if entry["event"] == "level"}
    assert levels == {p.slot: p.id for p in case.photos if p.slot is not None}
    unreadable = [entry for entry in logs if entry["event"] == "photo_unreadable"]
    assert [entry["photo_id"] for entry in unreadable] == [broken]
    for entry in logs:
        assert "Alex Wang" not in repr(entry)
        assert "full mouth" not in repr(entry)
