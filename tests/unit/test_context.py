"""The app context: startup cleanup, settings changes, and shared caches (spec §4.3, §4.8)."""

from __future__ import annotations

import threading
from pathlib import Path

import numpy as np
import pytest

from dental_image_organizer.core.align import PreparedReference
from dental_image_organizer.core.imageio import DecodedImage, RGBImage
from dental_image_organizer.errors import JobRunningError
from dental_image_organizer.services import originals
from dental_image_organizer.services.context import AppContext
from dental_image_organizer.services.intake import add_photos
from dental_image_organizer.services.jobs import JobProgress
from dental_image_organizer.services.originals import working_copy
from dental_image_organizer.storage.cache import cache_key
from support.app import jpeg, make_context, upload, wait
from support.synthetic import make_scene


def _entries(ctx: AppContext) -> list[Path]:
    return [p for p in ctx.dirs.renders.glob("??/*") if not p.name.startswith(".")]


def test_startup_cleans_temporary_folders(tmp_path: Path) -> None:
    exports = tmp_path / "cache" / "exports"
    session = tmp_path / "cache" / "teach" / "0123abcd"
    staging = tmp_path / "library" / ".importing-x"
    for folder in (exports, session, staging):
        folder.mkdir(parents=True)
        (folder / "left.bin").write_bytes(b"left over")

    ctx = make_context(tmp_path)
    try:
        assert list(ctx.dirs.exports.iterdir()) == []
        assert list(ctx.dirs.teach.iterdir()) == []
        assert not staging.exists()
        assert ctx.library().root == tmp_path / "library"
        assert ctx.library().root.is_dir()
    finally:
        ctx.close()


def test_moving_the_library_waits_for_jobs(ctx: AppContext, tmp_path: Path) -> None:
    release = threading.Event()

    def blocked(progress: JobProgress) -> None:
        release.wait(30)

    job = ctx.jobs.submit("block", blocked, key="case")
    first = ctx.library()
    other = tmp_path / "other library"
    moved = ctx.settings.get().model_copy(update={"library_root": other})
    try:
        with pytest.raises(JobRunningError):
            ctx.apply_settings(moved)
        assert ctx.settings.get().library_root == tmp_path / "library"
        # Settings that leave the library where it is apply while a job runs.
        ctx.apply_settings(ctx.settings.get().model_copy(update={"jpeg_quality": 90}))
        assert ctx.library() is first
    finally:
        release.set()
    wait(ctx, job.id)

    saved = ctx.apply_settings(ctx.settings.get().model_copy(update={"library_root": other}))

    assert saved.library_root == ctx.settings.get().library_root == other
    assert saved.jpeg_quality == 90
    assert ctx.library().root == other and other.is_dir()
    assert ctx.library() is not first and ctx.library() is ctx.library()


def test_cache_limit_follows_the_settings(ctx: AppContext) -> None:
    for i in range(4):
        ctx.cache.put(cache_key("entry", str(i)), bytes(1000))

    ctx.apply_settings(ctx.settings.get().model_copy(update={"cache_max_gb": 2.5e-6}))

    assert len(_entries(ctx)) == 2
    ctx.cache.put(cache_key("entry", "late"), bytes(1000))
    ctx.cache.evict()
    assert len(_entries(ctx)) == 2


def test_working_copies_and_references_are_cached(
    ctx: AppContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    library = ctx.library()
    patient = library.create_patient("Alex Wang", None)
    case_id = library.create_case(patient.id, "procedure", "implant 16").id
    data = jpeg(make_scene(5, 2400, 1600))
    added, _ = add_photos(ctx, case_id, [upload("IMG_0001.jpg", data)], role="reference")
    photo = library.read_case(case_id).photo(added[0])
    decodes: list[int] = []
    prepares: list[int] = []
    real_decode, real_prepare = originals.decode, originals.prepare_reference

    def counting_decode(raw: bytes) -> DecodedImage:
        decodes.append(1)
        return real_decode(raw)

    def counting_prepare(rgb: RGBImage) -> PreparedReference:
        prepares.append(1)
        return real_prepare(rgb)

    monkeypatch.setattr(originals, "decode", counting_decode)
    monkeypatch.setattr(originals, "prepare_reference", counting_prepare)

    first = working_copy(ctx, case_id, photo)
    second = working_copy(ctx, case_id, photo)
    assert len(decodes) == 1
    assert max(first.shape[:2]) == 1600
    assert np.array_equal(first, second)

    reference = ctx.references.get(ctx, case_id, photo)
    assert ctx.references.get(ctx, case_id, photo) is reference
    assert len(prepares) == 1
    assert reference.full_size == (2400, 1600)
