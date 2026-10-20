"""The shared AI runtime: one verified model, embedder, and classifier (spec §4.6)."""

from __future__ import annotations

import threading
import uuid
from pathlib import Path

import httpx
import pytest

from dental_image_organizer.ml.classifier import MIN_SAMPLES_PER_SLOT
from dental_image_organizer.ml.model_store import ModelSpec
from dental_image_organizer.ml.training import CLASSIFIER_FILE
from dental_image_organizer.paths import AppDirs
from dental_image_organizer.services import ai as ai_module
from dental_image_organizer.services.ai import AiRuntime, AssignReason
from dental_image_organizer.services.flips import FlipStore
from support.embeddings import synthetic_pairs
from support.fake_model import install_fake_model


def _dirs(tmp_path: Path) -> AppDirs:
    return AppDirs(tmp_path / "config", tmp_path / "data", tmp_path / "cache", tmp_path / "log")


def _runtime(tmp_path: Path, *, install: bool = True) -> tuple[AiRuntime, ModelSpec]:
    dirs = _dirs(tmp_path)
    spec = install_fake_model(dirs.models)
    if not install:
        (dirs.models / spec.filename).unlink()
    return AiRuntime(dirs, spec), spec


def _ids(count: int) -> list[str]:
    return [uuid.uuid4().hex for _ in range(count)]


def test_no_model_means_no_embedder(tmp_path: Path) -> None:
    runtime, _ = _runtime(tmp_path, install=False)
    assert runtime.model_path() is None
    assert runtime.embedder() is None


def test_the_model_is_verified_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime, _ = _runtime(tmp_path)
    calls: list[int] = []
    real = ai_module.cached_model

    def counting(spec: ModelSpec, cache_dir: Path) -> Path | None:
        calls.append(1)
        return real(spec, cache_dir)

    monkeypatch.setattr(ai_module, "cached_model", counting)
    for _ in range(3):
        assert runtime.model_path() is not None
    assert len(calls) == 1
    first = runtime.embedder()
    assert first is not None
    assert runtime.embedder() is first


def test_a_changed_model_file_is_verified_again(tmp_path: Path) -> None:
    runtime, spec = _runtime(tmp_path)
    assert runtime.model_path() is not None
    (runtime.dirs.models / spec.filename).write_bytes(b"short")
    assert runtime.model_path() is None


def test_download_reports_progress_and_verifies(tmp_path: Path) -> None:
    source, spec = _runtime(tmp_path / "source")
    payload = (source.dirs.models / spec.filename).read_bytes()
    runtime = AiRuntime(
        _dirs(tmp_path / "target"),
        spec,
        http_client=lambda: httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, content=payload))
        ),
    )
    assert runtime.model_path() is None
    reports: list[tuple[int, int]] = []
    runtime.download(lambda done, total: reports.append((done, total)))
    assert reports[-1] == (spec.size_bytes, spec.size_bytes)
    assert runtime.model_path() is not None


def test_three_learned_sets_train_the_classifier(tmp_path: Path) -> None:
    runtime, _ = _runtime(tmp_path)
    pairs, slots = synthetic_pairs(MIN_SAMPLES_PER_SLOT, 1)
    assert len(slots) == 27
    assert runtime.learn(pairs, slots, photo_ids=_ids(27), case_id="c1", source="export")
    classifier, reason = runtime.classifier()
    assert classifier is not None
    assert reason is None
    status = runtime.status()
    assert status.trained
    assert status.model_available
    assert status.damaged is None
    assert set(status.counts.values()) == {MIN_SAMPLES_PER_SLOT}


@pytest.mark.parametrize(
    ("content", "installed", "reason"),
    [
        (None, True, "untrained"),
        (b"{}", False, "no_model"),
        (b'{"model_hash": "older"}', True, "stale_model"),
        (b"{", True, "damaged_classifier"),
    ],
)
def test_classifier_reasons(
    tmp_path: Path, content: bytes | None, installed: bool, reason: AssignReason
) -> None:
    runtime, _ = _runtime(tmp_path, install=installed)
    if content is not None:
        runtime.dirs.classifier.mkdir(parents=True)
        (runtime.dirs.classifier / CLASSIFIER_FILE).write_bytes(content)
    assert runtime.classifier() == (None, reason)
    assert not runtime.status().trained


def test_concurrent_learning_keeps_every_sample(tmp_path: Path) -> None:
    runtime, _ = _runtime(tmp_path)
    pairs, slots = synthetic_pairs(1, 2)
    errors: list[BaseException] = []

    def work() -> None:
        try:
            runtime.learn(pairs, slots, photo_ids=_ids(9), case_id=uuid.uuid4().hex, source="teach")
        except BaseException as exc:  # surfaced below; a thread would swallow it
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    assert sum(runtime.status().counts.values()) == 18


def test_flips_are_recorded_under_the_runtime(tmp_path: Path) -> None:
    runtime, _ = _runtime(tmp_path)
    runtime.record_flips("c1", {"upper_right": "none"}, pins={})
    assert isinstance(runtime.flips, FlipStore)
    assert [c.case_id for c in runtime.flips.load()] == ["c1"]
