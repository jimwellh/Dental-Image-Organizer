import hashlib
import io
import re
import shutil
import unicodedata
from pathlib import Path
from typing import Any

import click
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
from dental_image_organizer.core.slots import DEFAULT_FLIPS, GROUPS, SLOTS
from dental_image_organizer.errors import ModelUnavailableError
from dental_image_organizer.instance import InstanceLock
from dental_image_organizer.ml.classifier import SlotClassifier
from dental_image_organizer.ml.embedder import EMBEDDING_DIM, Embedder
from dental_image_organizer.ml.model_store import ModelSpec
from dental_image_organizer.ml.samples import SampleStore
from dental_image_organizer.services import composite, headless_composite
from dental_image_organizer.services.headless_composite import (
    DAMAGED_CLASSIFIER_NOTE,
    DUPLICATE_PHOTO_NOTE,
    CompositeOptions,
    find_photo,
    resolve_assign,
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
    return _invoke_photos(sorted(set_dir.iterdir()), out, *extra)


def _invoke_photos(photos: list[Path], out: Path, *extra: str) -> Result:
    return CliRunner().invoke(
        app,
        [
            "composite",
            *(str(p) for p in photos),
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
    assert "(empty)" not in result.output and "Colors were matched" not in result.output


def test_empty_slots_and_a_baseline_fallback_are_reported(set_dir: Path, tmp_path: Path) -> None:
    result = _invoke(
        set_dir, tmp_path / "out", "--assign", "IMG_0009.jpg=tray", "--baseline", "lower_left"
    )

    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    # Empty slots keep their place in the grid order of the cell lines.
    assert [line.split()[0] for line in lines[:9]] == list(SLOTS)
    assert lines[8] == f"{'lower_left':<16} {'':>5}  (empty)"
    assert "IMG_0009.jpg" in _lines(result)["tray"]
    assert "Colors were matched to upper_right because lower_left has no usable photo." in lines


def test_a_baseline_photo_that_fails_is_reported_as_unusable(
    set_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_normalize_view = composite.normalize_view

    def normalize_failing_on_the_baseline(image: np.ndarray, slot: str, *rest: Any) -> Any:
        if slot == "occlusion_front":
            raise ValueError("synthetic leveling failure")
        return real_normalize_view(image, slot, *rest)

    monkeypatch.setattr(composite, "normalize_view", normalize_failing_on_the_baseline)

    result = _invoke(set_dir, tmp_path / "out")

    assert result.exit_code == 1, result.output
    assert "IMG_0005.jpg: Unexpected ValueError" in _lines(result)["failed"]
    assert (
        "Colors were matched to upper_right because occlusion_front has no usable photo."
        in result.output.splitlines()
    )


def test_nothing_is_written_when_no_photo_could_be_placed(
    set_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_model(tmp_path, monkeypatch)
    out = tmp_path / "out"

    result = _invoke_photos(
        [set_dir / "IMG_0001.jpg"], out, "--assign", "IMG_0001.jpg=tray", "--learn"
    )

    assert result.exit_code == 1, result.output
    assert f"{'composite':<16} {'':>5}  not written: no photo could be placed" in (
        result.output.splitlines()
    )
    assert not out.exists()
    assert "Learned" not in result.output
    assert not (tmp_path / "data" / "classifier" / "samples.npz").exists()


def test_assign_swaps_two_photos(set_dir: Path, tmp_path: Path) -> None:
    result = _invoke(set_dir, tmp_path / "out", "--assign", "IMG_0001.jpg=upper_left")

    assert result.exit_code == 0, result.output
    lines = _lines(result)
    assert "IMG_0001.jpg" in lines["upper_left"]
    assert "IMG_0003.jpg" in lines["upper_right"]


def test_assign_accepts_a_path_to_the_photo(set_dir: Path, tmp_path: Path) -> None:
    result = _invoke(
        set_dir, tmp_path / "out", "--assign", f"{set_dir / 'IMG_0001.jpg'}=upper_left"
    )

    assert result.exit_code == 0, result.output
    lines = _lines(result)
    assert "IMG_0001.jpg" in lines["upper_left"]
    assert "IMG_0003.jpg" in lines["upper_right"]


def test_a_file_name_shared_by_two_photos_asks_for_the_path(set_dir: Path, tmp_path: Path) -> None:
    retake = tmp_path / "retakes" / "IMG_0001.jpg"
    retake.parent.mkdir()
    retake.write_bytes((set_dir / "IMG_0001.jpg").read_bytes())
    photos = [*sorted(set_dir.glob("*.jpg")), retake]

    result = _invoke_photos(photos, tmp_path / "out", "--assign", "IMG_0001.jpg=upper_left")

    assert result.exit_code == 2, result.output
    # The usage error is boxed and wrapped, and colored where Typer forces a terminal (it does
    # under GITHUB_ACTIONS); compare its words without styles or box-drawing characters.
    plain = re.sub("[─-╿]", " ", click.unstyle(result.output))
    words = " ".join(plain.split())
    assert "'IMG_0001.jpg' is the name of 2 photos; pass its path instead" in words
    assert not (tmp_path / "out").exists()


def test_assign_names_match_by_file_name_or_path(tmp_path: Path) -> None:
    # macOS can hand over decomposed names while the typed name is composed, or the reverse.
    decomposed = tmp_path / unicodedata.normalize("NFD", "café.jpg")
    plain = tmp_path / "IMG_0001.jpg"
    for photo in (plain, decomposed):
        photo.write_bytes(b"photo")
    photos = [plain, decomposed]

    assert find_photo("IMG_0001.jpg", photos) == 0
    assert find_photo(str(plain), photos) == 0
    assert find_photo(unicodedata.normalize("NFC", "café.jpg"), photos) == 1
    with pytest.raises(ValueError, match="is not one of the photos"):
        find_photo("IMG_0002.jpg", photos)
    with pytest.raises(ValueError, match="is not one of the photos"):
        find_photo(str(tmp_path / "IMG_0002.jpg"), photos)


def test_resolve_assign_rejects_a_photo_named_twice_or_two_photos_in_one_slot(
    tmp_path: Path,
) -> None:
    photos = [tmp_path / "IMG_0001.jpg", tmp_path / "IMG_0002.jpg"]
    for photo in photos:
        photo.write_bytes(b"photo")

    assert resolve_assign([("IMG_0002.jpg", "upper_left"), ("IMG_0001.jpg", None)], photos) == {
        1: "upper_left",
        0: None,
    }
    # A file name and a path to the same file name one photo.
    with pytest.raises(ValueError, match=re.escape("'IMG_0001.jpg' is assigned twice")):
        resolve_assign([(str(photos[0]), None), ("IMG_0001.jpg", "upper_left")], photos)
    with pytest.raises(ValueError, match="two files are assigned to upper_left"):
        resolve_assign([("IMG_0001.jpg", "upper_left"), ("IMG_0002.jpg", "upper_left")], photos)


def test_the_service_takes_assignments_by_input_index(set_dir: Path, tmp_path: Path) -> None:
    photos = sorted(set_dir.glob("*.jpg"))

    report = run_composite(
        photos,
        tmp_path / "out",
        patient="A",
        tag="B",
        options=CompositeOptions(assign={0: "upper_left"}),
    )

    cells = {cell.slot: cell.source.name for cell in report.cells}
    assert cells["upper_left"] == "IMG_0001.jpg" and cells["upper_right"] == "IMG_0003.jpg"


@pytest.mark.parametrize(
    "assign",
    [{9: "upper_left"}, {0: "upper_left", 1: "upper_left"}],
    ids=["out-of-range", "one-slot-twice"],
)
def test_a_bad_assignment_fails_before_any_work(
    set_dir: Path, tmp_path: Path, assign: dict[int, Any]
) -> None:
    photos = sorted(set_dir.glob("*.jpg"))

    with pytest.raises(ValueError):
        run_composite(
            photos, tmp_path / "out", patient="A", tag="B", options=CompositeOptions(assign=assign)
        )
    assert not (tmp_path / "out").exists()


def test_photos_are_hashed_only_to_learn(
    set_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hashed: list[Path] = []
    real_load_photo_with_id = headless_composite.load_photo_with_id

    def spy(path: Path) -> Any:
        hashed.append(path)
        return real_load_photo_with_id(path)

    monkeypatch.setattr(headless_composite, "load_photo_with_id", spy)

    assert _invoke(set_dir, tmp_path / "out").exit_code == 0
    assert hashed == []


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


def test_learning_refuses_while_the_app_runs(
    set_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_model(tmp_path, monkeypatch)
    lock = InstanceLock(paths.app_dirs().instance_lock)
    assert lock.try_acquire()
    try:
        refused = _invoke(set_dir, tmp_path / "out", "--learn")
        # A run that does not learn leaves the classifier alone, so it needs no lock.
        unlearned = _invoke(set_dir, tmp_path / "plain")
    finally:
        lock.release()

    assert refused.exit_code == 1, refused.output
    assert (
        "error: Dental Image Organizer is running; teach from the app, or quit it first."
        in refused.output
    )
    assert not (tmp_path / "out").exists()
    assert not paths.classifier_dir().exists()
    assert unlearned.exit_code == 0, unlearned.output


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
    # This run is the one that teaches it.
    assert "rerun with --learn" not in first.output
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


def test_a_failed_sample_write_still_reports_the_written_composite(
    set_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_model(tmp_path, monkeypatch)

    def disk_full(self: SampleStore, _: Any) -> None:
        raise OSError(28, "No space left on device", str(self.path))

    monkeypatch.setattr(SampleStore, "_write", disk_full)

    result = _invoke(set_dir, tmp_path / "out", "--learn")

    assert result.exit_code == 1, result.output
    assert isinstance(result.exception, SystemExit), result.exception
    assert f"-> {OUTPUT}" in _lines(result)["composite"]
    assert (
        "Learning failed: No space left on device. The composite was written; nothing was learned."
        in result.output.splitlines()
    )
    # The data folder sits in the user's home folder. Only the report on stdout is checked:
    # result.output also carries the stderr traceback, which names the file by design.
    assert str(tmp_path) not in result.stdout


def test_one_photo_placed_in_two_slots_is_not_learned(
    set_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_model(tmp_path, monkeypatch)
    retake = tmp_path / "retakes" / "IMG_0001.jpg"
    retake.parent.mkdir()
    retake.write_bytes((set_dir / "IMG_0001.jpg").read_bytes())
    photos = [*sorted(set_dir.glob("*.jpg")), retake]

    result = _invoke_photos(photos, tmp_path / "out", "--learn", "--assign", f"{retake}=upper_left")

    assert result.exit_code == 0, result.output
    assert DUPLICATE_PHOTO_NOTE in result.output
    assert "Learned 7 samples" in result.output
    samples = SampleStore(tmp_path / "data" / "classifier").load()
    assert len(samples.photo_ids) == len(set(samples.photo_ids)) == 7
    assert hashlib.sha256(retake.read_bytes()).hexdigest() not in samples.photo_ids


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
    assert (
        "Downloading the AI model failed: offline. Slots were assigned by shooting order."
        in result.output.splitlines()
    )
    assert (tmp_path / "out" / OUTPUT).exists()


def _install_classifier(tmp_path: Path) -> None:
    """A trained classifier for the installed fake model, so a run needs the model."""
    d, g = EMBEDDING_DIM, len(GROUPS)
    model_hash = headless_composite.DEFAULT_MODEL.sha256
    SlotClassifier(model_hash, GROUPS, np.zeros((g, d)), np.zeros(g), np.zeros(d)).save(
        tmp_path / "data" / "classifier" / "model.json"
    )


def test_a_model_that_cannot_be_loaded_falls_back_to_shooting_order(
    set_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_model(tmp_path, monkeypatch)
    _install_classifier(tmp_path)

    def broken_runtime(*_: Any) -> Any:
        raise ModelUnavailableError("Cannot load the AI model: broken runtime")

    monkeypatch.setattr(headless_composite, "Embedder", broken_runtime)

    result = _invoke(set_dir, tmp_path / "out")

    assert result.exit_code == 0, result.output
    assert (
        "Cannot load the AI model: broken runtime. Slots were assigned by shooting order."
        in result.output.splitlines()
    )
    assert "IMG_0001.jpg" in _lines(result)["upper_right"]
    assert (tmp_path / "out" / OUTPUT).exists()


def _fail_inference(monkeypatch: pytest.MonkeyPatch) -> None:
    def out_of_memory(*_: Any) -> Any:
        # onnxruntime raises its own exception types, which are not DioError or OSError, and
        # its messages often end with a period of their own.
        raise RuntimeError("allocation failed.")

    monkeypatch.setattr(Embedder, "embed", out_of_memory)


def test_a_model_that_fails_on_the_photos_falls_back_to_shooting_order(
    set_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_model(tmp_path, monkeypatch)
    _install_classifier(tmp_path)
    _fail_inference(monkeypatch)

    result = _invoke(set_dir, tmp_path / "out")

    assert result.exit_code == 0, result.output
    assert (
        "Running the AI model failed: Unexpected RuntimeError: allocation failed. "
        "Slots were assigned by shooting order." in result.output.splitlines()
    )
    assert "IMG_0001.jpg" in _lines(result)["upper_right"]
    assert (tmp_path / "out" / OUTPUT).exists()


def test_learning_stops_before_any_output_when_the_model_fails_on_the_photos(
    set_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_model(tmp_path, monkeypatch)
    _fail_inference(monkeypatch)

    result = _invoke(set_dir, tmp_path / "out", "--learn")

    assert result.exit_code == 1, result.output
    assert isinstance(result.exception, SystemExit), result.exception
    assert "error: Running the AI model failed: Unexpected RuntimeError" in result.output
    assert not (tmp_path / "out").exists()


# IMG_0001 has no decoded photo before it, so its rank comes from the one after it.
@pytest.mark.parametrize("broken", [5, 1])
def test_corrupt_photo_leaves_its_slot_empty(set_dir: Path, tmp_path: Path, broken: int) -> None:
    copy = tmp_path / "set"
    shutil.copytree(set_dir, copy)
    damaged = copy / f"IMG_{broken:04d}.jpg"
    damaged.write_bytes(damaged.read_bytes()[:300])

    result = _invoke(copy, tmp_path / "out")

    assert result.exit_code == 1, result.output
    lines = _lines(result)
    assert f"IMG_{broken:04d}.jpg: " in lines["failed"]
    assert (tmp_path / "out" / OUTPUT).exists()
    # The unreadable photo keeps its rank in the shooting order, so no later photo moves up.
    for number, slot in enumerate(SLOTS, start=1):
        expected = "(empty)" if number == broken else f"IMG_{number:04d}.jpg"
        assert expected in lines[slot], lines[slot]


def test_an_unreadable_photo_ranks_by_file_name_whatever_the_input_order(
    set_dir: Path, tmp_path: Path
) -> None:
    copy = tmp_path / "set"
    shutil.copytree(set_dir, copy)
    # Nothing past the JPEG marker: no capture time survives.
    (copy / "IMG_0005.jpg").write_bytes(b"\xff\xd8\xff\xe0" + bytes(20))
    # Finder hands over a selection in the order it was clicked, not by name.
    photos = sorted(copy.glob("*.jpg"), reverse=True)

    result = _invoke_photos(photos, tmp_path / "out")

    assert result.exit_code == 1, result.output
    lines = _lines(result)
    assert "IMG_0005.jpg: " in lines["failed"]
    for number, slot in enumerate(SLOTS, start=1):
        expected = "(empty)" if number == 5 else f"IMG_{number:04d}.jpg"
        assert expected in lines[slot], lines[slot]


def test_an_unreadable_retake_with_the_same_name_ranks_after_the_original(
    set_dir: Path, tmp_path: Path
) -> None:
    retake = tmp_path / "retakes" / "IMG_0009.jpg"
    retake.parent.mkdir()
    retake.write_bytes(b"\xff\xd8\xff\xe0" + bytes(20))
    photos = [*sorted(set_dir.glob("*.jpg")), retake]

    result = _invoke_photos(photos, tmp_path / "out")

    assert result.exit_code == 1, result.output
    lines = _lines(result)
    assert "IMG_0009.jpg" in lines["lower_left"], lines["lower_left"]
    assert "tray" not in lines


def test_an_unreadable_photo_ranks_by_the_capture_time_left_in_its_header(
    set_dir: Path, tmp_path: Path
) -> None:
    copy = tmp_path / "set"
    shutil.copytree(set_dir, copy)
    first = (copy / "IMG_0001.jpg").read_bytes()
    (copy / "IMG_0001.jpg").unlink()
    # A name that sorts last: only the EXIF header can tell that it was shot first.
    (copy / "IMG_0100.jpg").write_bytes(first[: len(first) // 2])

    result = _invoke(copy, tmp_path / "out")

    assert result.exit_code == 1, result.output
    lines = _lines(result)
    assert "IMG_0100.jpg: " in lines["failed"]
    assert lines["upper_right"].endswith("(empty)")
    for number, slot in enumerate(SLOTS[1:], start=2):
        assert f"IMG_{number:04d}.jpg" in lines[slot], lines[slot]


@pytest.mark.usefixtures("debug_logging")
def test_planning_events_carry_input_positions(set_dir: Path, tmp_path: Path) -> None:
    jpegs = sorted(set_dir.glob("*.jpg"))
    # The skipped video makes every later photo's input position one more than its index.
    photos = [*jpegs[:4], set_dir / "IMG_0099.MOV", *jpegs[4:]]

    with capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs:
        run_composite(photos, tmp_path / "out", patient="A", tag="B")

    levels = {entry["slot"]: entry["photo"] for entry in logs if entry["event"] == "level"}
    assert levels == {slot: i + 1 if i < 4 else i + 2 for i, slot in enumerate(SLOTS)}


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
    assert lines["upper_left"].endswith("(empty)")
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
