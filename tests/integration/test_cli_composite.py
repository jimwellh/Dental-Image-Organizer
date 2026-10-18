import hashlib
import io
import shutil
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import structlog
from click.testing import Result
from PIL import ExifTags, Image
from structlog.testing import capture_logs
from typer.testing import CliRunner

from dental_image_organizer import paths
from dental_image_organizer.cli import app
from dental_image_organizer.core.geometry import flip_pixels
from dental_image_organizer.core.slots import DEFAULT_FLIPS, SLOTS
from dental_image_organizer.errors import ModelUnavailableError
from dental_image_organizer.ml.model_store import ModelSpec
from dental_image_organizer.ml.samples import SampleStore
from dental_image_organizer.services import composite, headless_composite
from dental_image_organizer.services.headless_composite import (
    DAMAGED_CLASSIFIER_NOTE,
    run_composite,
)
from support.fake_model import build_fake_model
from support.views import make_view

OUTPUT = "Alex Wang_full mouth_Composite_20261016.jpg"
VIDEO = b"\x00\x00\x00\x14ftypqt  \x00\x00\x00\x00qt  "


def _jpeg(rgb: np.ndarray, taken: str) -> bytes:
    exif = Image.Exif()
    exif.get_ifd(ExifTags.IFD.Exif)[ExifTags.Base.DateTimeOriginal] = taken
    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, format="JPEG", quality=92, exif=exif.tobytes())
    return buf.getvalue()


def _write_set(folder: Path, seed: int) -> Path:
    """One session shot in row-major slot order, as the camera stored it."""
    folder.mkdir(parents=True, exist_ok=True)
    for i, slot in enumerate(SLOTS):
        view = flip_pixels(make_view(slot, seed + i, angle=3.0), DEFAULT_FLIPS[slot])
        taken = f"2026:10:16 10:00:{10 + i:02d}"
        (folder / f"IMG_{i + 1:04d}.jpg").write_bytes(_jpeg(view, taken))
    return folder


@pytest.fixture(scope="module")
def set_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One session plus a video, which is skipped."""
    folder = _write_set(tmp_path_factory.mktemp("set"), 70)
    (folder / "IMG_0099.MOV").write_bytes(VIDEO)
    return folder


@pytest.fixture(autouse=True)
def app_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The classifier and the model cache must never touch the developer's real app folders.
    monkeypatch.setattr(paths, "data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(paths, "cache_dir", lambda: tmp_path / "cache")


def _invoke(set_dir: Path, out: Path, *extra: str) -> Result:
    photos = sorted(str(p) for p in set_dir.iterdir())
    return CliRunner().invoke(
        app,
        [
            "composite",
            *photos,
            "--out",
            str(out),
            "--patient",
            "Alex Wang",
            "--tag",
            "full mouth",
            *extra,
        ],
    )


def _lines(result: Result) -> dict[str, str]:
    """Report lines keyed by their first word (a slot, `tray`, `failed`, or `composite`)."""
    return {line.split()[0]: line for line in result.output.splitlines() if line.strip()}


def _fake_spec(model: Path) -> ModelSpec:
    sha = build_fake_model(model)
    return ModelSpec(model.name, "https://unused.test", sha, model.stat().st_size)


def _install_fake_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    model = tmp_path / "cache" / "models" / "fake.onnx"
    model.parent.mkdir(parents=True)
    monkeypatch.setattr(headless_composite, "DEFAULT_MODEL", _fake_spec(model))


def test_composite_by_shooting_order(set_dir: Path, tmp_path: Path) -> None:
    result = _invoke(set_dir, tmp_path / "out")

    assert result.exit_code == 0, result.output
    assert Image.open(tmp_path / "out" / OUTPUT).size == (3600, 2400)
    assert "Slots were assigned by shooting order" in result.output
    assert "Skipped 1 file: 1 video" in result.output
    assert "IMG_0001.jpg" in _lines(result)["upper_right"]


def test_assign_swaps_two_photos(set_dir: Path, tmp_path: Path) -> None:
    result = _invoke(set_dir, tmp_path / "out", "--assign", "IMG_0001.jpg=upper_left")

    assert result.exit_code == 0, result.output
    lines = _lines(result)
    assert "IMG_0001.jpg" in lines["upper_left"]
    assert "IMG_0003.jpg" in lines["upper_right"]


def test_custom_order_changes_the_placement(set_dir: Path, tmp_path: Path) -> None:
    result = _invoke(set_dir, tmp_path / "out", "--order", ",".join(reversed(SLOTS)))

    assert result.exit_code == 0, result.output
    assert "IMG_0001.jpg" in _lines(result)["lower_left"]


@pytest.mark.parametrize(
    "extra",
    [
        ("--order", "upper_right"),
        ("--flip", "upper_right=sideways"),
        ("--assign", "IMG_9999.jpg=upper_left"),
        ("--assign", "IMG_0001.jpg=upper_left", "--assign", "IMG_0002.jpg=upper_left"),
        ("--baseline", "middle"),
    ],
)
def test_invalid_options_are_usage_errors(
    set_dir: Path, tmp_path: Path, extra: tuple[str, ...]
) -> None:
    result = _invoke(set_dir, tmp_path / "out", *extra)

    assert result.exit_code == 2, result.output
    assert not (tmp_path / "out").exists()


def test_learning_without_the_model_is_an_error_before_writing(
    set_dir: Path, tmp_path: Path
) -> None:
    result = _invoke(set_dir, tmp_path / "out", "--learn")

    assert result.exit_code == 1, result.output
    assert "--download-model" in result.output
    assert not (tmp_path / "out").exists()


def test_three_learned_sets_train_the_classifier(
    set_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_model(tmp_path, monkeypatch)
    out = tmp_path / "out"
    second_set = _write_set(tmp_path / "set2", 80)
    third_set = _write_set(tmp_path / "set3", 90)

    first = _invoke(set_dir, out, "--learn")
    assert first.exit_code == 0, first.output
    assert "the AI classifier is not trained yet" in first.output
    assert _invoke(second_set, out, "--learn").exit_code == 0
    third = _invoke(third_set, out, "--learn")
    assert third.exit_code == 0, third.output
    assert "the AI classifier is trained" in third.output

    fourth = _invoke(set_dir, out)
    assert fourth.exit_code == 0, fourth.output
    assert "Slots were assigned by shooting order" not in fourth.output
    assert (tmp_path / "data" / "classifier" / "model.json").exists()


def test_relearning_a_set_replaces_its_earlier_labels(
    set_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_model(tmp_path, monkeypatch)
    out = tmp_path / "out"
    # Confirmed by mistake with the wrong shooting order, then corrected and confirmed again.
    wrong = _invoke(set_dir, out, "--learn", "--order", ",".join(reversed(SLOTS)))
    assert wrong.exit_code == 0, wrong.output

    fixed = _invoke(set_dir, out, "--learn")

    assert fixed.exit_code == 0, fixed.output
    assert "the AI classifier is not trained yet" in fixed.output
    samples = SampleStore(tmp_path / "data" / "classifier").load()
    expected = {
        hashlib.sha256((set_dir / f"IMG_{i + 1:04d}.jpg").read_bytes()).hexdigest(): slot
        for i, slot in enumerate(SLOTS)
    }
    assert dict(zip(samples.photo_ids, samples.slots, strict=True)) == expected
    assert len(samples.slots) == 9


def test_download_is_announced_and_only_with_consent(
    set_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(headless_composite, "DEFAULT_MODEL", _fake_spec(tmp_path / "fake.onnx"))
    downloads: list[Path] = []

    def fake_download(spec: ModelSpec, cache_dir: Path, **_: Any) -> Path:
        cache_dir.mkdir(parents=True, exist_ok=True)
        downloads.append(Path(shutil.copy(tmp_path / spec.filename, cache_dir / spec.filename)))
        return downloads[-1]

    monkeypatch.setattr(headless_composite, "download_model", fake_download)

    refused = _invoke(set_dir, tmp_path / "out", "--learn")
    assert refused.exit_code == 1 and downloads == []

    result = _invoke(set_dir, tmp_path / "out", "--learn", "--download-model")

    assert result.exit_code == 0, result.output
    assert "Downloading the AI model" in result.output
    assert "from github.com" in result.output
    assert downloads == [tmp_path / "cache" / "models" / "fake.onnx"]


def test_failed_download_falls_back_to_shooting_order(
    set_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "data" / "classifier").mkdir(parents=True)
    (tmp_path / "data" / "classifier" / "model.json").write_text("{}", encoding="utf-8")

    def offline(*_: Any, **__: Any) -> Path:
        raise ModelUnavailableError("Downloading the AI model failed: offline")

    monkeypatch.setattr(headless_composite, "download_model", offline)

    result = _invoke(set_dir, tmp_path / "out", "--download-model")

    assert result.exit_code == 0, result.output
    assert "Downloading the AI model failed: offline" in result.output
    assert "Slots were assigned by shooting order." in result.output
    assert (tmp_path / "out" / OUTPUT).exists()


def test_corrupt_photo_leaves_its_slot_empty(set_dir: Path, tmp_path: Path) -> None:
    copy = tmp_path / "set"
    shutil.copytree(set_dir, copy)
    broken = copy / "IMG_0005.jpg"
    broken.write_bytes(broken.read_bytes()[:300])

    result = _invoke(copy, tmp_path / "out")

    assert result.exit_code == 1, result.output
    assert "IMG_0005.jpg: " in result.output
    assert (tmp_path / "out" / OUTPUT).exists()


def test_photo_that_fails_planning_leaves_its_slot_empty(
    set_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_normalize_view = composite.normalize_view

    def normalize_failing_on_upper_left(image: np.ndarray, slot: str, *rest: Any) -> Any:
        if slot == "upper_left":
            raise ValueError("synthetic leveling failure")
        return real_normalize_view(image, slot, *rest)

    monkeypatch.setattr(composite, "normalize_view", normalize_failing_on_upper_left)

    result = _invoke(set_dir, tmp_path / "out")

    assert result.exit_code == 1, result.output
    assert isinstance(result.exception, SystemExit), result.exception
    lines = _lines(result)
    assert "upper_left" not in lines
    assert "IMG_0003.jpg: Unexpected ValueError: synthetic leveling failure" in lines["failed"]
    assert (tmp_path / "out" / OUTPUT).exists()


def test_no_usable_photos_is_an_error(set_dir: Path, tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "composite",
            str(set_dir / "IMG_0099.MOV"),
            "--out",
            str(tmp_path / "out"),
            "--patient",
            "A",
            "--tag",
            "B",
        ],
    )

    assert result.exit_code == 1, result.output
    assert "No usable photos" in result.output


def test_classifier_of_an_older_model_asks_for_new_samples(
    set_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_model(tmp_path, monkeypatch)
    (tmp_path / "data" / "classifier").mkdir(parents=True)
    (tmp_path / "data" / "classifier" / "model.json").write_text(
        '{"model_hash": "an-older-model"}', encoding="utf-8"
    )

    result = _invoke(set_dir, tmp_path / "out")

    assert result.exit_code == 0, result.output
    assert "The AI model changed" in result.output
    assert "Slots were assigned by shooting order:" not in result.output


def test_a_damaged_classifier_falls_back_to_shooting_order(
    set_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_model(tmp_path, monkeypatch)
    (tmp_path / "data" / "classifier").mkdir(parents=True)
    (tmp_path / "data" / "classifier" / "model.json").write_text("", encoding="utf-8")

    result = _invoke(set_dir, tmp_path / "out")

    assert result.exit_code == 0, result.output
    assert DAMAGED_CLASSIFIER_NOTE in result.output
    assert "Slots were assigned by shooting order:" not in result.output
    assert (tmp_path / "out" / OUTPUT).exists()


def test_learning_drops_a_damaged_classifier_it_cannot_rebuild_yet(
    set_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_model(tmp_path, monkeypatch)
    model_file = tmp_path / "data" / "classifier" / "model.json"
    model_file.parent.mkdir(parents=True)
    model_file.write_text("", encoding="utf-8")

    result = _invoke(set_dir, tmp_path / "out", "--learn")

    assert result.exit_code == 0, result.output
    assert DAMAGED_CLASSIFIER_NOTE in result.output
    assert "the AI classifier is not trained yet" in result.output
    # Left in place, it would be reported as damaged on every later run.
    assert not model_file.exists()


def test_a_damaged_sample_store_stops_learning_before_any_output(
    set_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_model(tmp_path, monkeypatch)
    folder = tmp_path / "data" / "classifier"
    folder.mkdir(parents=True)
    (folder / "samples.npz").write_bytes(b"")

    result = _invoke(set_dir, tmp_path / "out", "--learn")

    assert result.exit_code == 1, result.output
    assert (
        f"The AI sample store in {folder} is damaged; move that folder aside to start over."
        in result.output
    )
    assert not (tmp_path / "out").exists()


def test_a_second_run_never_overwrites_the_first_composite(set_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    assert _invoke(set_dir, out).exit_code == 0
    first = (out / OUTPUT).read_bytes()

    result = _invoke(set_dir, out, "--assign", "IMG_0001.jpg=upper_left")

    assert result.exit_code == 0, result.output
    assert (out / OUTPUT).read_bytes() == first
    assert (out / OUTPUT.replace(".jpg", "_2.jpg")).exists()


@pytest.mark.usefixtures("debug_logging")
def test_logs_and_messages_never_name_the_patient(set_dir: Path, tmp_path: Path) -> None:
    # Patient folders and re-processed exports put the patient's name into every path.
    folder = tmp_path / "Alex Wang"
    folder.mkdir()
    photos = []
    for source in sorted(set_dir.glob("*.jpg")):
        photos.append(folder / f"Alex Wang {source.name}")
        photos[-1].write_bytes(source.read_bytes())
    # A directory with a photo's name makes read_bytes() raise an OSError on every OS.
    unreadable = folder / "Alex Wang IMG_0010.jpg"
    unreadable.mkdir()

    with capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs:
        report = run_composite(
            [*photos, unreadable], tmp_path / "out", patient="Alex Wang", tag="full mouth"
        )

    assert len(report.cells) == 9
    assert any(entry["event"] == "level" for entry in logs)
    assert all("Alex Wang" not in repr(entry) for entry in logs)
    assert [p.status for p in report.problems] == ["failed"]
    assert "Alex Wang" not in str(report.problems[0].message)
