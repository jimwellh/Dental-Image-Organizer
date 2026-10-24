"""Teaching sessions: past composite sets become samples and flips, outside the library (§3.5)."""

from __future__ import annotations

import io
import threading
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from dental_image_organizer.core.imageio import RGBImage
from dental_image_organizer.core.slots import SLOTS
from dental_image_organizer.errors import (
    InvalidRequestError,
    ModelUnavailableError,
    NotReadyError,
    TeachSessionNotFoundError,
)
from dental_image_organizer.ml.embedder import EmbeddingPair
from dental_image_organizer.ml.samples import SampleStore
from dental_image_organizer.services.context import AppContext
from dental_image_organizer.services.teach import TeachSession
from support.app import make_context, upload, view_set, wait
from support.fake_model import install_fake_model

Files = list[tuple[str, bytes]]


@pytest.fixture(scope="module")
def nine() -> Files:
    return view_set()


@pytest.fixture
def taught(tmp_path: Path) -> Iterator[AppContext]:
    spec = install_fake_model(tmp_path / "cache" / "models")
    context = make_context(tmp_path, model_spec=spec)
    yield context
    context.close()


def _start(ctx: AppContext, files: Files) -> TeachSession:
    session, skipped = ctx.teach.start([upload(name, data) for name, data in files])
    assert session is not None and not skipped
    wait(ctx, session.job_id)
    ready = ctx.teach.get(session.id)
    assert ready.state == "ready"
    return ready


def _ids(session: TeachSession) -> dict[str, str]:
    return {p.name: p.id for p in session.photos}


def test_three_taught_sets_train_the_classifier(taught: AppContext) -> None:
    for seed in (70, 170, 270):
        session = _start(taught, view_set(seed))
        assert dict(session.proposal) == dict(
            zip(SLOTS, [p.id for p in session.photos[:9]], strict=True)
        )
        status = taught.teach.confirm(session.id, session.proposal, {})
    assert status.trained
    samples = SampleStore(taught.dirs.classifier).load()
    assert len(samples.slots) == 27
    assert set(samples.sources) == {"teach"}
    assert taught.library().listing().patients == ()
    assert list(taught.dirs.teach.iterdir()) == []


def test_the_proposal_follows_the_shooting_order(tmp_path: Path, nine: Files) -> None:
    spec = install_fake_model(tmp_path / "cache" / "models")
    ctx = make_context(tmp_path, model_spec=spec, shooting_order=list(reversed(SLOTS)))
    try:
        session = _start(ctx, nine)
        assert session.proposal["lower_left"] == _ids(session)["IMG_0001.jpg"]
    finally:
        ctx.close()


def test_cancel_deletes_the_session(taught: AppContext, nine: Files) -> None:
    session = _start(taught, nine)
    taught.teach.cancel(session.id)
    with pytest.raises(TeachSessionNotFoundError):
        taught.teach.get(session.id)
    assert list(taught.dirs.teach.iterdir()) == []


def test_cancel_while_processing_does_not_bring_the_folder_back(
    taught: AppContext, nine: Files, monkeypatch: pytest.MonkeyPatch
) -> None:
    started, release = threading.Event(), threading.Event()
    _hold_embedding(taught, monkeypatch, started, release)
    session, _ = taught.teach.start([upload(n, d) for n, d in nine])
    assert session is not None
    assert started.wait(30)
    taught.teach.cancel(session.id)
    release.set()
    taught.jobs.wait(session.job_id, timeout=60)
    assert list(taught.dirs.teach.iterdir()) == []
    with pytest.raises(TeachSessionNotFoundError):
        taught.teach.get(session.id)


def test_teaching_needs_the_model(ctx: AppContext, nine: Files) -> None:
    with pytest.raises(ModelUnavailableError):
        ctx.teach.start([upload(n, d) for n, d in nine])
    assert list(ctx.dirs.teach.iterdir()) == []


def test_an_incomplete_set_adds_only_its_slots(taught: AppContext) -> None:
    session = _start(taught, view_set(count=7))
    status = taught.teach.confirm(session.id, session.proposal, {})
    assert len(session.proposal) == 7
    assert sorted(status.counts.values()) == [0, 0, 1, 1, 1, 1, 1, 1, 1]


def test_confirmed_flips_feed_flip_learning(taught: AppContext, nine: Files) -> None:
    session = _start(taught, nine)
    taught.teach.confirm(session.id, session.proposal, {"upper_right": "none"})
    entries = [c for c in taught.ai.flips.load() if c.case_id == f"teach:{session.id}"]
    assert len(entries) == 9
    assert {c.slot: c.flip for c in entries}["upper_right"] == "none"


def test_invalid_confirmations_are_rejected(
    taught: AppContext, nine: Files, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _start(taught, nine)
    a = session.proposal["upper_left"]
    for slots, flips in (
        ({"upper_left": a, "upper_right": a}, {}),
        ({"upper_left": "nope"}, {}),
        ({"upper_left": a}, {"upper_right": "none"}),
        ({}, {}),
    ):
        with pytest.raises(InvalidRequestError):
            taught.teach.confirm(session.id, slots, flips)  # type: ignore[arg-type]
    # Rejected confirmations leave the session as it was.
    assert taught.teach.get(session.id).state == "ready"

    started, release = threading.Event(), threading.Event()
    _hold_embedding(taught, monkeypatch, started, release)
    busy, _ = taught.teach.start([upload(n, d) for n, d in nine])
    assert busy is not None
    assert started.wait(30)
    try:
        with pytest.raises(NotReadyError):
            taught.teach.confirm(busy.id, {}, {})
    finally:
        release.set()
        taught.jobs.wait(busy.job_id, timeout=60)


def test_a_confirm_retried_after_a_failure_learns_each_photo_once(
    taught: AppContext, nine: Files, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _start(taught, nine)
    real = taught.ai.record_flips
    calls: list[int] = []

    def flaky(*args: Any, **kwargs: Any) -> None:
        calls.append(1)
        if len(calls) == 1:
            raise OSError(28, "No space left on device")
        real(*args, **kwargs)

    monkeypatch.setattr(taught.ai, "record_flips", flaky)
    with pytest.raises(OSError, match="No space"):
        taught.teach.confirm(session.id, session.proposal, {})
    assert taught.teach.get(session.id).state == "ready"
    taught.teach.confirm(session.id, session.proposal, {})
    assert len(SampleStore(taught.dirs.classifier).load().slots) == 9
    entries = [c for c in taught.ai.flip_history() if c.case_id == f"teach:{session.id}"]
    assert len(entries) == 9
    with pytest.raises(TeachSessionNotFoundError):
        taught.teach.get(session.id)


def test_thumbnails(taught: AppContext, nine: Files) -> None:
    session = _start(taught, nine)
    photo = session.photos[0]
    thumb = Image.open(io.BytesIO(taught.teach.image(session.id, photo.id, "thumb")))
    assert thumb.format == "JPEG"
    assert max(thumb.size) == 320
    preview = Image.open(io.BytesIO(taught.teach.image(session.id, photo.id, "preview")))
    assert max(preview.size) == 1600


def test_repeats_and_unsupported_files_are_skipped(taught: AppContext, nine: Files) -> None:
    files = [*nine, ("again.jpg", nine[0][1]), ("notes.txt", b"hello")]
    session, skipped = taught.teach.start([upload(n, d) for n, d in files])
    assert session is not None
    assert [s.reason for s in skipped] == ["duplicate", "unknown"]
    assert len(session.photos) == 9
    nothing, skipped = taught.teach.start([upload("notes.txt", b"hello")])
    assert nothing is None and len(skipped) == 1
    wait(taught, session.job_id)


def _hold_embedding(
    ctx: AppContext,
    monkeypatch: pytest.MonkeyPatch,
    started: threading.Event,
    release: threading.Event,
) -> None:
    real = ctx.ai.embedder()
    assert real is not None

    class Held:
        def embed(self, images: Sequence[RGBImage]) -> EmbeddingPair:
            started.set()
            release.wait(60)
            return real.embed(images)

    monkeypatch.setattr(ctx.ai, "embedder", lambda: Held())
