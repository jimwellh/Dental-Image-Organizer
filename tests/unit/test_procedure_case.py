"""Procedure cases in the library: reference, alignment, stages, and edits (spec §3.2, §4.8)."""

from __future__ import annotations

import hashlib
import math
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
import structlog
from structlog.testing import capture_logs

from dental_image_organizer.core.align import AlignModel
from dental_image_organizer.errors import (
    InvalidEditError,
    InvalidRequestError,
    OutputTooLargeError,
)
from dental_image_organizer.services import procedure, procedure_case
from dental_image_organizer.services.context import AppContext
from dental_image_organizer.services.intake import UploadOutcome
from dental_image_organizer.services.jobs import JobProgress
from dental_image_organizer.services.procedure_case import (
    reference_edit,
    sequence_photos,
    suggested_output_scale,
)
from dental_image_organizer.storage.library import Library
from dental_image_organizer.storage.schema import (
    CaseRecord,
    CompositeRecord,
    SequenceRecord,
    new_id,
)
from support.app import (
    SCENE_SIZE,
    ProcedureSet,
    jpeg,
    make_context,
    procedure_set,
    upload,
    wait,
)
from support.scenarios import about_center, make_pair
from support.synthetic import make_scene

SIZE = (1200, 800)
VANISHED = "The photo's file was removed while it was being added; drop it again."


@pytest.fixture(scope="module")
def photos() -> ProcedureSet:
    return procedure_set()


def _case(ctx: AppContext) -> str:
    library = ctx.library()
    patient = library.create_patient("Alex Wang", None)
    return library.create_case(patient.id, "procedure", "implant 16").id


def _read(ctx: AppContext, case_id: str) -> CaseRecord:
    return ctx.library().read_case(case_id)


def _upload(ctx: AppContext, case_id: str, *files: tuple[str, bytes]) -> list[str]:
    outcome = procedure_case.upload(ctx, case_id, [upload(name, data) for name, data in files])
    assert outcome.job_id is not None
    wait(ctx, outcome.job_id)
    return list(outcome.added)


def _set_reference(
    ctx: AppContext, case_id: str, data: bytes, name: str = "reference.jpg"
) -> UploadOutcome:
    outcome = procedure_case.set_reference(ctx, case_id, upload(name, data))
    assert outcome.job_id is not None
    wait(ctx, outcome.job_id)
    return outcome


def _aligned(ctx: AppContext, photos: ProcedureSet) -> tuple[str, str, str]:
    """A case with the reference set and `intra` and `post` aligned to it."""
    case_id = _case(ctx)
    _set_reference(ctx, case_id, photos.reference)
    intra, post = _upload(
        ctx, case_id, ("IMG_0001.jpg", photos.intra), ("IMG_0002.jpg", photos.post)
    )
    return case_id, intra, post


def _later_photo() -> bytes:
    return jpeg(make_scene(62, 600, 400), "2026:10:25 10:00:00")


@pytest.fixture(scope="module")
def shared(
    tmp_path_factory: pytest.TempPathFactory, photos: ProcedureSet
) -> Iterator[tuple[AppContext, str, str]]:
    """One aligned case for the tests that must leave it unchanged."""
    ctx = make_context(tmp_path_factory.mktemp("shared"))
    try:
        case_id, intra, _ = _aligned(ctx, photos)
        yield ctx, case_id, intra
    finally:
        ctx.close()


def test_photos_wait_for_the_reference_then_align(ctx: AppContext, photos: ProcedureSet) -> None:
    case_id = _case(ctx)
    intra, post = _upload(
        ctx, case_id, ("IMG_0001.jpg", photos.intra), ("IMG_0002.jpg", photos.post)
    )

    case = _read(ctx, case_id)
    assert [(p.state, p.edit, p.stage) for p in (case.photo(intra), case.photo(post))] == [
        ("ready", None, "intra_op"),
        ("ready", None, "post_op"),
    ]
    first = case.photo(intra)
    assert (first.width, first.height) == SCENE_SIZE
    assert (first.captured_at, first.captured_at_source) == (datetime(2026, 10, 16, 10), "exif")

    outcome = _set_reference(ctx, case_id, photos.reference)

    case = _read(ctx, case_id)
    reference = case.reference()
    assert reference is not None
    assert outcome.added == (reference.id,)
    assert (reference.state, reference.stage, reference.stage_source) == ("ready", "pre_op", "auto")
    assert (reference.width, reference.height) == SIZE
    assert reference.edit == reference_edit(SIZE)
    for photo_id in (intra, post):
        edit = case.photo(photo_id).edit
        assert edit is not None
        assert (edit.status, edit.source, edit.output_size) == ("ok", "auto", SIZE)
        assert edit.confidence is not None and edit.confidence >= 0.6
        assert edit.algorithm_version == "align-1"
    assert [case.photo(i).stage for i in (intra, post)] == ["intra_op", "post_op"]


def test_manual_stages_survive_new_photos(ctx: AppContext, photos: ProcedureSet) -> None:
    case_id = _case(ctx)
    intra, post = _upload(
        ctx, case_id, ("IMG_0001.jpg", photos.intra), ("IMG_0002.jpg", photos.post)
    )

    changed = procedure_case.set_stage(ctx, case_id, post, "intra_op")
    (third,) = _upload(ctx, case_id, ("IMG_0003.jpg", _later_photo()))

    case = _read(ctx, case_id)
    assert (changed.photo(post).stage, changed.photo(post).stage_source) == ("intra_op", "manual")
    assert (case.photo(post).stage, case.photo(post).stage_source) == ("intra_op", "manual")
    assert case.photo(intra).stage == "intra_op"
    assert (case.photo(third).stage, case.photo(third).stage_source) == ("post_op", "auto")


def test_replacing_the_reference_reprocesses_and_drops_manual_edits(
    ctx: AppContext, photos: ProcedureSet
) -> None:
    case_id, intra, post = _aligned(ctx, photos)
    edited = procedure_case.apply_manual_edit(ctx, case_id, intra, photos.truth, "similarity")
    old = edited.reference()
    assert old is not None
    old_file = ctx.library().case_dir(case_id) / old.file
    procedure_case.set_sequence(ctx, case_id, [old.id, intra, post], 0)
    width, height = SCENE_SIZE
    scene = make_scene(61, width, height)
    second = make_pair(scene, about_center(width, height, 1.0, 0.0), box=(500, 300, 1200, 800))

    outcome = _set_reference(ctx, case_id, jpeg(second.reference, "2026:10:16 09:00:00"))

    case = _read(ctx, case_id)
    reference = case.reference()
    assert reference is not None
    assert outcome.added == (reference.id,)
    assert {p.id for p in case.photos} == {intra, post, reference.id}
    assert reference.edit == reference_edit(SIZE)
    edit = case.photo(intra).edit
    assert edit is not None
    assert (edit.source, edit.status) == ("auto", "ok")
    assert edit.transform != list(photos.truth)
    assert case.photo(post).edit is not None
    assert case.sequence == SequenceRecord(photo_ids=[intra, post], gap_px=0)
    assert not old_file.exists()


def test_a_photo_dropped_as_reference_becomes_the_reference(
    ctx: AppContext, photos: ProcedureSet
) -> None:
    case_id = _case(ctx)
    (photo_id,) = _upload(ctx, case_id, ("IMG_0000.jpg", photos.reference))

    outcome = _set_reference(ctx, case_id, photos.reference)

    case = _read(ctx, case_id)
    assert outcome.added == ()
    assert outcome.skipped == ()
    assert [(p.id, p.role, p.stage) for p in case.photos] == [(photo_id, "reference", "pre_op")]
    assert case.photos[0].edit == reference_edit(SIZE)
    assert len(list((ctx.library().case_dir(case_id) / "originals").iterdir())) == 1


def test_a_reference_whose_staged_file_vanished_is_recorded_failed(
    ctx: AppContext, photos: ProcedureSet, monkeypatch: pytest.MonkeyPatch
) -> None:
    case_id = _case(ctx)
    (intra,) = _upload(ctx, case_id, ("IMG_0001.jpg", photos.intra))
    sha = hashlib.sha256(photos.reference).hexdigest()
    lost = ctx.library().case_dir(case_id) / "originals" / f"{sha[:16]}.jpg"
    real_update_case = Library.update_case
    deleted: list[Path] = []

    def deleting_first(
        self: Library, case_id: str, change: Callable[[CaseRecord], CaseRecord]
    ) -> CaseRecord:
        # A concurrent delete of a photo with the same bytes, between staging and the change.
        if not deleted:
            lost.unlink()
            deleted.append(lost)
        return real_update_case(self, case_id, change)

    monkeypatch.setattr(Library, "update_case", deleting_first)
    release = threading.Event()

    def blocked(progress: JobProgress) -> None:
        release.wait(30)

    # Holds the case's job queue, so the reference job cannot process the record yet.
    blocker = ctx.jobs.submit("block", blocked, key=case_id)
    try:
        outcome = procedure_case.set_reference(ctx, case_id, upload("ref.jpg", photos.reference))
        reference = _read(ctx, case_id).reference()
    finally:
        release.set()
    wait(ctx, blocker.id)
    assert outcome.job_id is not None
    wait(ctx, outcome.job_id)

    assert len(deleted) == 1
    assert reference is not None
    assert outcome.added == (reference.id,)
    assert (reference.state, reference.error) == ("failed", VANISHED)
    case = _read(ctx, case_id)
    assert case.photo(reference.id).state == "failed"
    assert (case.photo(intra).state, case.photo(intra).edit) == ("ready", None)


def test_an_unrelated_photo_needs_review(ctx: AppContext, photos: ProcedureSet) -> None:
    case_id = _case(ctx)
    _set_reference(ctx, case_id, photos.reference)

    (photo_id,) = _upload(
        ctx, case_id, ("IMG_0001.jpg", jpeg(make_scene(99), "2026:10:16 10:00:00"))
    )

    edit = _read(ctx, case_id).photo(photo_id).edit
    assert edit is not None
    assert (edit.status, edit.source, edit.output_size) == ("needs_review", "auto", SIZE)
    assert edit.color.method == "identity"
    assert edit.message


def test_a_corrupt_photo_fails_alone(ctx: AppContext, photos: ProcedureSet) -> None:
    case_id = _case(ctx)
    _set_reference(ctx, case_id, photos.reference)

    outcome = procedure_case.upload(
        ctx,
        case_id,
        [upload("IMG_0001.jpg", photos.intra), upload("IMG_0002.jpg", photos.post[:300])],
    )
    assert outcome.job_id is not None
    snapshot = wait(ctx, outcome.job_id)

    intra, post = outcome.added
    case = _read(ctx, case_id)
    broken = case.photo(post)
    assert (broken.state, broken.edit) == ("failed", None)
    assert broken.error
    edit = case.photo(intra).edit
    assert edit is not None and edit.status == "ok"
    assert snapshot.result == {
        "photos": [
            {"photo_id": intra, "state": "ready", "status": "ok", "error": None},
            {"photo_id": post, "state": "failed", "status": None, "error": broken.error},
        ]
    }


def test_manual_edit_refits_colors_and_is_kept(ctx: AppContext, photos: ProcedureSet) -> None:
    case_id, intra, _ = _aligned(ctx, photos)
    before = _read(ctx, case_id)
    auto = before.photo(intra).edit
    assert auto is not None and auto.color.method == "lab_quantile_v1"

    case = procedure_case.apply_manual_edit(ctx, case_id, intra, photos.truth, "similarity")

    manual = case.photo(intra).edit
    assert manual is not None
    assert (manual.source, manual.status, manual.message, manual.model) == (
        "manual",
        "ok",
        None,
        "similarity",
    )
    assert manual.transform == list(photos.truth)
    assert manual.output_size == SIZE
    assert manual.color.method == "lab_quantile_v1"
    assert manual.color.params != auto.color.params
    assert (manual.confidence, manual.detail_ratio) == (auto.confidence, auto.detail_ratio)
    assert _read(ctx, case_id) == case

    _upload(ctx, case_id, ("IMG_0003.jpg", _later_photo()))
    assert _read(ctx, case_id).photo(intra).edit == manual

    reset = procedure_case.reset_edit(ctx, case_id, intra).photo(intra).edit
    assert reset is not None
    assert (reset.source, reset.status) == ("auto", "ok")
    reference = before.reference()
    assert reference is not None
    with pytest.raises(InvalidRequestError):
        procedure_case.reset_edit(ctx, case_id, reference.id)


def test_processing_keeps_manual_edits_and_skips_deleted_photos(
    ctx: AppContext, photos: ProcedureSet
) -> None:
    case_id, intra, post = _aligned(ctx, photos)
    case = procedure_case.apply_manual_edit(ctx, case_id, intra, photos.truth, "similarity")
    manual = case.photo(intra).edit
    gone = new_id()
    calls: list[tuple[int, int]] = []

    def progress(done: int, total: int, message: str | None = None) -> None:
        calls.append((done, total))

    # A job that started before the manual edit reaches the photo only now.
    result = procedure_case.process_photos(ctx, case_id, [intra, gone, post], progress)

    after = _read(ctx, case_id)
    assert after.photo(intra).edit == manual
    assert [p["photo_id"] for p in result["photos"]] == [intra, post]
    assert result["photos"][0] == {
        "photo_id": intra,
        "state": "ready",
        "status": "ok",
        "error": None,
    }
    assert calls == [(1, 3), (2, 3), (3, 3)]


SINGULAR_PERSPECTIVE = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [-1.5, 0.0, 1.0]]


@pytest.mark.parametrize(
    ("values", "model"),
    [
        pytest.param([1.0, 0, 0, 0, 1, 0, 0, 0], "homography", id="eight-values"),
        pytest.param([1.0, 0, 0, 0, math.nan, 0, 0, 0, 1], "homography", id="nan"),
        pytest.param([0.0] * 9, "homography", id="all-zeros"),
        pytest.param([0.5, 0, 0, 0, 0.5, 0, 0.001, 0, 1], "similarity", id="similarity-row"),
        pytest.param(
            [float(v) for v in np.linalg.inv(SINGULAR_PERSPECTIVE).ravel()],
            "homography",
            id="behind-the-camera",
        ),
    ],
)
def test_invalid_manual_edits_are_rejected(
    shared: tuple[AppContext, str, str], values: list[float], model: AlignModel
) -> None:
    ctx, case_id, intra = shared
    before = _read(ctx, case_id)

    with pytest.raises(InvalidEditError):
        procedure_case.apply_manual_edit(ctx, case_id, intra, values, model)

    assert _read(ctx, case_id) == before


def test_output_scale_updates_every_edit_and_rejects_oversize(
    ctx: AppContext, photos: ProcedureSet, monkeypatch: pytest.MonkeyPatch
) -> None:
    case_id, _, _ = _aligned(ctx, photos)
    transforms = [p.edit.transform for p in _read(ctx, case_id).photos if p.edit]

    doubled = procedure_case.set_output_scale(ctx, case_id, 2.0)

    assert doubled.output_scale == 2.0
    assert [p.edit.output_size for p in doubled.photos if p.edit] == [(2400, 1600)] * 3
    assert [p.edit.transform for p in doubled.photos if p.edit] == transforms
    assert _read(ctx, case_id) == doubled

    procedure_case.set_output_scale(ctx, case_id, 1.0)
    monkeypatch.setattr(procedure, "MAX_JPEG_DIMENSION", 2000)
    with pytest.raises(OutputTooLargeError):
        procedure_case.set_output_scale(ctx, case_id, 2.0)

    case = _read(ctx, case_id)
    assert case.output_scale == 1.0
    assert [p.edit.output_size for p in case.photos if p.edit] == [SIZE] * 3
    with pytest.raises(InvalidRequestError):
        procedure_case.set_output_scale(ctx, case_id, 3.0)


def test_a_small_reference_suggests_a_larger_output(ctx: AppContext, photos: ProcedureSet) -> None:
    small, _, _ = _aligned(ctx, procedure_set(reference_scale=0.4))
    usual, _, _ = _aligned(ctx, photos)

    case = _read(ctx, small)
    ratios = [p.edit.detail_ratio for p in case.photos if p.edit and p.role == "photo"]
    assert all(r is not None and r > 2.0 for r in ratios)
    assert suggested_output_scale(case) == 2.0
    assert suggested_output_scale(_read(ctx, usual)) is None
    assert suggested_output_scale(procedure_case.set_output_scale(ctx, small, 2.0)) is None


def test_sequence_selection(ctx: AppContext, photos: ProcedureSet) -> None:
    case_id = _case(ctx)
    _set_reference(ctx, case_id, photos.reference)
    post, intra = _upload(
        ctx, case_id, ("IMG_0002.jpg", photos.post), ("IMG_0001.jpg", photos.intra)
    )
    # No EXIF time: the upload's own time, which carries an offset, has to sort among naive ones.
    extra_time = datetime(2026, 10, 30, 9, tzinfo=UTC)
    extra = procedure_case.upload(
        ctx, case_id, [upload("IMG_0003.jpg", jpeg(make_scene(63, 600, 400)), extra_time)]
    )
    assert extra.job_id is not None
    wait(ctx, extra.job_id)
    case = _read(ctx, case_id)
    reference = case.reference()
    assert reference is not None

    assert [p.id for p in sequence_photos(case)] == [reference.id, intra, post, *extra.added]

    chosen = procedure_case.set_sequence(ctx, case_id, [post, reference.id], 0)
    assert chosen.sequence == SequenceRecord(photo_ids=[post, reference.id], gap_px=0)
    assert [p.id for p in sequence_photos(chosen)] == [post, reference.id]
    for ids, gap in (
        ([post], 0),
        ([post, post], 0),
        ([post, new_id()], 0),
        ([post, reference.id], 201),
    ):
        with pytest.raises(InvalidRequestError):
            procedure_case.set_sequence(ctx, case_id, ids, gap)
    assert _read(ctx, case_id) == chosen

    default = procedure_case.set_sequence(ctx, case_id, [], 12)
    assert default.sequence == SequenceRecord(photo_ids=[], gap_px=12)
    assert [p.id for p in sequence_photos(default)] == [reference.id, intra, post, *extra.added]


def test_composite_operations_are_refused(ctx: AppContext, photos: ProcedureSet) -> None:
    library = ctx.library()
    patient = library.create_patient("Alex Wang", None)
    layout = CompositeRecord(
        baseline_photo_id=None,
        cell_size=(1200, 800),
        gap_px=0,
        gap_color="#000000",
        slots_source="auto",
    )
    case_id = library.create_case(patient.id, "composite", "full mouth", composite=layout).id

    with pytest.raises(InvalidRequestError, match="composite case"):
        procedure_case.upload(ctx, case_id, [upload("IMG_0001.jpg", photos.intra)])
    with pytest.raises(InvalidRequestError, match="composite case"):
        procedure_case.set_reference(ctx, case_id, upload("ref.jpg", photos.reference))
    with pytest.raises(InvalidRequestError, match="composite case"):
        procedure_case.set_output_scale(ctx, case_id, 1.5)
    with pytest.raises(InvalidRequestError, match="composite case"):
        procedure_case.set_sequence(ctx, case_id, [], 0)

    assert library.read_case(case_id).photos == []
    assert list(library.case_dir(case_id).rglob("*.jpg")) == []


@pytest.mark.usefixtures("debug_logging")
def test_processing_logs_never_name_the_patient(ctx: AppContext, photos: ProcedureSet) -> None:
    case_id = _case(ctx)

    # capture_logs() swaps the processor chain, so contextvars are merged explicitly here.
    with capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs:
        _set_reference(ctx, case_id, photos.reference, "Alex Wang pre-op.jpg")
        _, broken = _upload(
            ctx, case_id, ("Alex Wang 1.jpg", photos.intra), ("Alex Wang 2.jpg", photos.post[:300])
        )

    assert any(entry.get("photo_id") for entry in logs)
    unreadable = [entry for entry in logs if entry["event"] == "photo_unreadable"]
    assert [entry["photo_id"] for entry in unreadable] == [broken]
    for entry in logs:
        assert "Alex Wang" not in repr(entry)
        assert "implant" not in repr(entry)
