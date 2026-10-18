"""File-based Feature 2 batch behind `dio composite` (spec §3.3, §4.6, §4.10)."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import structlog

from dental_image_organizer import paths
from dental_image_organizer.core.imageio import RGBImage, encode_jpeg, resize_long_edge
from dental_image_organizer.core.naming import (
    composite_capture_date,
    composite_filename,
    sanitize_component,
)
from dental_image_organizer.core.slots import DEFAULT_FLIPS, SLOTS, Flip, Slot
from dental_image_organizer.errors import (
    CorruptImageError,
    DioError,
    ModelIntegrityError,
    ModelUnavailableError,
    UnsupportedFormatError,
)
from dental_image_organizer.ml.assignment import AssignMethod, with_overrides
from dental_image_organizer.ml.classifier import SlotClassifier, load_classifier, train_classifier
from dental_image_organizer.ml.embedder import Embedder, EmbeddingPair
from dental_image_organizer.ml.model_store import DEFAULT_MODEL, cached_model, download_model
from dental_image_organizer.ml.samples import SampleStore
from dental_image_organizer.services.batch_io import (
    MTIME_WARNING,
    capture_time,
    load_photo,
    os_error_message,
    unexpected_message,
    write_unique,
)
from dental_image_organizer.services.composite import (
    DEFAULT_BASELINE,
    DEFAULT_CELL_SIZE,
    CellPlan,
    assign_photos,
    compose,
    plan_composite,
    render_cell,
)
from dental_image_organizer.services.headless import PhotoOutcome

log = structlog.get_logger(__name__)

WORKING_LONG_EDGE = 1600
CLASSIFIER_FILE = "model.json"

ORDER_NOTE = (
    "Slots were assigned by shooting order: the AI classifier needs 3 confirmed samples per "
    "slot. Check the result, fix it with --assign, and rerun with --learn to teach it."
)
NO_MODEL_NOTE = (
    "The AI classifier is trained, but the AI model is not downloaded; slots were assigned by "
    "shooting order. Rerun with --download-model."
)
DOWNLOAD_FAILED_NOTE = "Slots were assigned by shooting order."
STALE_MODEL_NOTE = (
    "The AI model changed since the classifier was trained, so its samples no longer apply; "
    "confirm 3 sets again with --learn. Slots were assigned by shooting order."
)
LEARN_NEEDS_MODEL = (
    "--learn needs the AI model (downloaded once from github.com); rerun with --download-model."
)
NO_PHOTOS = "No usable photos to compose."


def _ignore(_message: str) -> None:
    return None


@dataclass(frozen=True)
class CompositeOptions:
    """`assign` maps input file names to a slot, or to None for the tray."""

    order: Sequence[Slot] = SLOTS
    flips: Mapping[Slot, Flip] = field(default_factory=lambda: dict(DEFAULT_FLIPS))
    baseline: Slot = DEFAULT_BASELINE
    assign: Mapping[str, Slot | None] = field(default_factory=dict)
    learn: bool = False
    download_model: bool = False


@dataclass(frozen=True)
class CellOutcome:
    slot: Slot
    source: Path
    confidence: float | None
    level_deg: float
    leveled: bool
    flip: Flip


@dataclass(frozen=True)
class CompositeReport:
    output: Path
    cells: tuple[CellOutcome, ...]
    tray: tuple[Path, ...]
    problems: tuple[PhotoOutcome, ...]
    method: AssignMethod
    low_confidence: frozenset[Slot]
    notes: tuple[str, ...]
    learned: int
    trained: bool

    @property
    def failed(self) -> bool:
        return any(p.status == "failed" for p in self.problems)


@dataclass(frozen=True)
class _AiState:
    classifier: SlotClassifier | None
    embedder: Embedder | None
    notes: tuple[str, ...]


@dataclass(frozen=True)
class _Working:
    """A decoded photo kept as a small working copy; `number` is its 1-based input position."""

    number: int
    source: Path
    image: RGBImage
    taken: datetime
    from_file: bool


def run_composite(
    photo_paths: Sequence[Path],
    out_dir: Path,
    *,
    patient: str,
    tag: str,
    options: CompositeOptions | None = None,
    today: date | None = None,
    notify: Callable[[str], None] = _ignore,
) -> CompositeReport:
    options = options or CompositeOptions()
    # Validate before any work so a bad name or option cannot leave a half-written export.
    sanitize_component(patient)
    sanitize_component(tag)
    _check_assign(options.assign, photo_paths)
    # The AI state comes before decoding so a --learn without the model fails before writing.
    ai = _resolve_ai(options, notify)

    problems: dict[int, PhotoOutcome] = {}
    workings: list[_Working] = []
    for number, path in enumerate(photo_paths, start=1):
        # Events are tagged with a position, never a file name: patient folders and exports
        # carry patient names (spec §4.13).
        with structlog.contextvars.bound_contextvars(photo=number):
            loaded = _load_working(number, path)
        if isinstance(loaded, PhotoOutcome):
            problems[number] = loaded
        else:
            workings.append(loaded)
    if not workings:
        raise DioError(NO_PHOTOS)

    images = [w.image for w in workings]
    embeddings = ai.embedder.embed(images) if ai.embedder is not None else None
    assignment = assign_photos(
        [w.taken for w in workings], options.order, classifier=ai.classifier, embeddings=embeddings
    )
    # A name of a photo that failed to decode is skipped: that photo is reported already.
    index = {w.source.name: i for i, w in enumerate(workings)}
    assignment = with_overrides(
        assignment,
        {index[name]: slot for name, slot in options.assign.items() if name in index},
    )
    notes = list(ai.notes)
    if assignment.method == "order" and not ai.notes:
        notes.append(ORDER_NOTE)
    if any(w.from_file for w in workings):
        notes.append(MTIME_WARNING)

    plan = plan_composite(images, assignment, flips=options.flips, baseline=options.baseline)
    for photo, exc in plan.failed.items():
        working = workings[photo]
        problems[working.number] = PhotoOutcome(
            working.source, "failed", message=_failure_message(exc)
        )

    rendered: dict[Slot, RGBImage] = {}
    cells: list[CellOutcome] = []
    for slot, cell in plan.cells.items():
        working = workings[cell.photo]
        with structlog.contextvars.bound_contextvars(photo=working.number):
            result = _render_full(working.source, cell)
        if isinstance(result, PhotoOutcome):
            problems[working.number] = result
            continue
        rendered[slot] = result
        cells.append(_cell_outcome(cell, working.source))

    out_dir.mkdir(parents=True, exist_ok=True)
    taken = {p.name for p in out_dir.iterdir()}
    dates = [workings[plan.cells[slot].photo].taken.date() for slot in rendered]
    name = composite_filename(
        patient, tag, composite_capture_date(dates or [today or date.today()])
    )
    output = write_unique(out_dir, taken, name, encode_jpeg(compose(rendered)))

    learned, trained = 0, False
    if options.learn:
        # _resolve_ai always creates an embedder for --learn, or raises.
        assert ai.embedder is not None and embeddings is not None
        placed = [plan.cells[slot].photo for slot in rendered]
        trained = _learn(embeddings.take(placed), list(rendered), ai.embedder.model_hash)
        learned = len(placed)

    return CompositeReport(
        output=output,
        cells=tuple(cells),
        tray=tuple(workings[i].source for i in plan.tray),
        problems=tuple(problems[n] for n in sorted(problems)),
        method=plan.method,
        low_confidence=plan.low_confidence,
        notes=tuple(notes),
        learned=learned,
        trained=trained,
    )


def _check_assign(assign: Mapping[str, Slot | None], photo_paths: Sequence[Path]) -> None:
    names = {p.name for p in photo_paths}
    unknown = sorted(set(assign) - names)
    if unknown:
        raise ValueError(f"--assign names files that are not inputs: {', '.join(unknown)}")
    targets = [slot for slot in assign.values() if slot is not None]
    if len(targets) != len(set(targets)):
        raise ValueError("--assign puts two files in the same slot")


def _resolve_ai(options: CompositeOptions, notify: Callable[[str], None]) -> _AiState:
    # Read at call time, not import time, so a test can swap in the fake model.
    spec = DEFAULT_MODEL
    classifier_file = paths.classifier_dir() / CLASSIFIER_FILE
    need_model = options.learn or classifier_file.exists()
    model_path = cached_model(spec, paths.model_cache_dir()) if need_model else None
    notes: list[str] = []
    download_failed = False
    if need_model and model_path is None and options.download_model:
        notify(f"Downloading the AI model ({spec.size_bytes // 1_000_000} MB) from github.com ...")
        try:
            model_path = download_model(spec, paths.model_cache_dir())
        except (ModelUnavailableError, ModelIntegrityError) as exc:
            if options.learn:
                raise
            # Offline or a bad download must not block the composite (spec §4.6).
            notes.append(f"{exc} {DOWNLOAD_FAILED_NOTE}")
            download_failed = True
    if options.learn and model_path is None:
        raise ModelUnavailableError(LEARN_NEEDS_MODEL)

    classifier: SlotClassifier | None = None
    if model_path is None:
        # After a failed download, "rerun with --download-model" would be the wrong advice.
        if classifier_file.exists() and not download_failed:
            notes.append(NO_MODEL_NOTE)
    else:
        classifier = load_classifier(classifier_file, spec.sha256)
        if classifier is None and classifier_file.exists():
            notes.append(STALE_MODEL_NOTE)
    embedder = (
        Embedder(model_path, spec.sha256)
        if model_path is not None and (classifier is not None or options.learn)
        else None
    )
    return _AiState(classifier, embedder, tuple(notes))


def _load_working(number: int, path: Path) -> _Working | PhotoOutcome:
    """Decode one photo into a working copy; a failure is recorded, never raised (spec §4.13).

    Only the working copy outlives this call, so one full-size photo is in memory at a time.
    """
    try:
        photo = load_photo(path)
        taken, from_file = capture_time(photo, path)
        image, _ = resize_long_edge(photo.rgb, WORKING_LONG_EDGE)
    except UnsupportedFormatError as exc:
        return PhotoOutcome(path, "skipped", message=str(exc), skip_reason=exc.reason)
    except CorruptImageError as exc:
        log.error("photo_unreadable", error=str(exc))
        return PhotoOutcome(path, "failed", message=str(exc))
    except OSError as exc:
        log.error("photo_unreadable", error=os_error_message(exc))
        return PhotoOutcome(path, "failed", message=os_error_message(exc))
    except Exception as exc:
        # Decoders parse untrusted bytes and hold several full-size copies, so a plugin error
        # or a MemoryError must fail only this photo.
        log.exception("photo_unreadable")
        return PhotoOutcome(path, "failed", message=unexpected_message(exc))
    return _Working(number, path, image, taken, from_file)


def _render_full(path: Path, cell: CellPlan) -> RGBImage | PhotoOutcome:
    """Render a cell from a fresh full-resolution decode; a failure leaves the cell empty."""
    try:
        return render_cell(load_photo(path).rgb, cell, DEFAULT_CELL_SIZE)
    except DioError as exc:
        # The file changed on disk since it was first decoded.
        log.error("photo_failed", error=str(exc))
        return PhotoOutcome(path, "failed", message=str(exc))
    except OSError as exc:
        log.error("photo_failed", error=os_error_message(exc))
        return PhotoOutcome(path, "failed", message=os_error_message(exc))
    except Exception as exc:
        # An OpenCV assertion or a MemoryError on a large photo fails only this cell; the trace
        # is logged for fixing the cause.
        log.exception("photo_failed")
        return PhotoOutcome(path, "failed", message=unexpected_message(exc))


def _failure_message(exc: Exception) -> str:
    # plan_composite has logged the trace already; this only words the reported message.
    return os_error_message(exc) if isinstance(exc, OSError) else unexpected_message(exc)


def _cell_outcome(cell: CellPlan, source: Path) -> CellOutcome:
    level = cell.geometry.level
    return CellOutcome(
        cell.slot, source, cell.confidence, level.angle_deg, level.confident, cell.geometry.flip
    )


def _learn(pairs: EmbeddingPair, slots: Sequence[Slot], model_hash: str) -> bool:
    """Store the placed photos as confirmed samples and retrain; True once the classifier is."""
    directory = paths.classifier_dir()
    # A random case id keeps patient names out of the data folder.
    samples = SampleStore(directory).append(
        pairs,
        slots,
        case_id=uuid.uuid4().hex,
        source="export",
        model_hash=model_hash,
        timestamp=time.time(),
    )
    current = samples.for_model(model_hash)
    classifier = train_classifier(current.pairs, current.slots, model_hash)
    if classifier is None:
        return False
    classifier.save(directory / CLASSIFIER_FILE)
    return True
