"""File-based Feature 2 batch behind `dio composite` (spec §3.3, §4.6, §4.10)."""

from __future__ import annotations

import os
import time
import unicodedata
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
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
    StorageError,
    UnsupportedFormatError,
)
from dental_image_organizer.ml.assignment import (
    Assignment,
    AssignMethod,
    assign_by_order,
    with_overrides,
)
from dental_image_organizer.ml.classifier import SlotClassifier, load_classifier, train_classifier
from dental_image_organizer.ml.embedder import Embedder, EmbeddingPair
from dental_image_organizer.ml.model_store import DEFAULT_MODEL, cached_model, download_model
from dental_image_organizer.ml.samples import SampleStore
from dental_image_organizer.services.batch_io import (
    MTIME_WARNING,
    capture_time,
    load_photo,
    load_photo_with_id,
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
DAMAGED_CLASSIFIER_NOTE = (
    "The AI classifier file is damaged and was ignored; slots were assigned by shooting order. "
    "Rerun with --learn to rebuild it."
)
LEARN_NEEDS_MODEL = (
    "--learn needs the AI model (downloaded once from github.com); rerun with --download-model."
)
NO_PHOTOS = "No usable photos to compose."


def _ignore(_message: str) -> None:
    return None


@dataclass(frozen=True)
class CompositeOptions:
    """`assign` maps a photo, by file name or path (see `find_photo`), to a slot or None (tray)."""

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
    """`output` is None when no cell could be rendered, so nothing was written.

    `baseline` is the slot the colors were matched to; None when no cell was planned.
    """

    output: Path | None
    baseline: Slot | None
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
    """A decoded photo kept as a small working copy.

    `number` is its 1-based input position; `photo_id` is the SHA-256 of its file.
    """

    number: int
    source: Path
    photo_id: str
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
    notify: Callable[[str], None] = _ignore,
) -> CompositeReport:
    options = options or CompositeOptions()
    # Validate before any work so a bad name or option cannot leave a half-written export.
    sanitize_component(patient)
    sanitize_component(tag)
    assign = _resolve_assign(options.assign, photo_paths)
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
    unreadable = [number for number, p in problems.items() if p.status == "failed"]
    if ai.classifier is not None and embeddings is not None:
        # An unreadable photo has no embedding, so the classifier simply never places it.
        assignment = assign_photos(
            [w.taken for w in workings],
            options.order,
            classifier=ai.classifier,
            embeddings=embeddings,
        )
    else:
        assignment = _order_keeping_holes(workings, unreadable, options.order)
    # An --assign entry for a photo that failed to decode is ignored: it is reported already.
    index = {w.number - 1: i for i, w in enumerate(workings)}
    assignment = with_overrides(
        assignment, {index[photo]: slot for photo, slot in assign.items() if photo in index}
    )
    notes = list(ai.notes)
    if assignment.method == "order" and not ai.notes:
        notes.append(ORDER_NOTE)
    if any(w.from_file for w in workings):
        notes.append(MTIME_WARNING)

    plan = plan_composite(
        images,
        assignment,
        flips=options.flips,
        baseline=options.baseline,
        positions=[w.number for w in workings],
    )
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

    # An all-black grid is no composite: with no cell rendered, nothing is written or learned.
    output: Path | None = None
    if rendered:
        out_dir.mkdir(parents=True, exist_ok=True)
        taken = {p.name for p in out_dir.iterdir()}
        dates = [workings[plan.cells[slot].photo].taken.date() for slot in rendered]
        name = composite_filename(patient, tag, composite_capture_date(dates))
        output = write_unique(out_dir, taken, name, encode_jpeg(compose(rendered)))

    learned, trained = 0, False
    if options.learn and rendered:
        # _resolve_ai always creates an embedder for --learn, or raises.
        assert ai.embedder is not None and embeddings is not None
        placed = [plan.cells[slot].photo for slot in rendered]
        trained = _learn(
            embeddings.take(placed),
            list(rendered),
            [workings[i].photo_id for i in placed],
            ai.embedder.model_hash,
        )
        learned = len(placed)

    return CompositeReport(
        output=output,
        baseline=plan.baseline,
        cells=tuple(cells),
        tray=tuple(workings[i].source for i in plan.tray),
        problems=tuple(problems[n] for n in sorted(problems)),
        method=plan.method,
        low_confidence=plan.low_confidence,
        notes=tuple(notes),
        learned=learned,
        trained=trained,
    )


def find_photo(name: str, photo_paths: Sequence[Path]) -> int:
    """The index of the input that `name` means: its file name, or a path to it.

    Raises ValueError when no input matches, or when a file name is shared by several inputs.
    A path to a file that was passed twice means its first occurrence.
    """
    if Path(name).name != name:
        # A path names one file whatever its spelling, case, or Unicode form on disk.
        matches = [i for i, path in enumerate(photo_paths) if _same_file(name, path)]
    else:
        # macOS may hand over decomposed (NFD) names while typed names are composed (NFC).
        wanted = unicodedata.normalize("NFC", name)
        matches = [
            i
            for i, path in enumerate(photo_paths)
            if unicodedata.normalize("NFC", path.name) == wanted
        ]
        if len(matches) > 1:
            raise ValueError(
                f"{name!r} is the name of {len(matches)} photos; pass its path instead"
            )
    if not matches:
        raise ValueError(f"{name!r} is not one of the photos")
    return matches[0]


def _same_file(name: str, path: Path) -> bool:
    try:
        return os.path.samefile(name, path)
    except OSError:
        # A path that does not exist names no input.
        return False


def _resolve_assign(
    assign: Mapping[str, Slot | None], photo_paths: Sequence[Path]
) -> dict[int, Slot | None]:
    """`assign` keyed by input index; raises ValueError for a bad or contradictory entry."""
    resolved: dict[int, Slot | None] = {}
    for name, slot in assign.items():
        photo = find_photo(name, photo_paths)
        if photo in resolved:
            raise ValueError(f"--assign names {name!r} twice")
        resolved[photo] = slot
    targets = [slot for slot in resolved.values() if slot is not None]
    if len(targets) != len(set(targets)):
        raise ValueError("--assign puts two files in the same slot")
    return resolved


def _order_keeping_holes(
    workings: Sequence[_Working], unreadable: Sequence[int], order: Sequence[Slot]
) -> Assignment:
    """Shooting order in which each unreadable photo still holds its rank, leaving it empty.

    Without the hole, every later photo would move up one slot. An unreadable photo has no
    capture time, so it borrows that of the nearest decoded input before it (after it, if none
    is before); ties keep input order, so it ranks next to the photo it borrowed from.
    """
    taken = {w.number: w.taken for w in workings}
    decoded = sorted(taken)

    def stand_in(number: int) -> datetime:
        before = [n for n in decoded if n < number]
        return taken[before[-1] if before else decoded[0]]

    numbers = sorted([*decoded, *unreadable])
    times = [taken[n] if n in taken else stand_in(n) for n in numbers]
    by_number = dict(zip(numbers, assign_by_order(times, order).slots, strict=True))
    slots = tuple(by_number[w.number] for w in workings)
    return Assignment(slots, (None,) * len(slots), "order")


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
        try:
            classifier = load_classifier(classifier_file, spec.sha256)
        except StorageError:
            # The classifier is rebuilt from the samples, so a damaged file only costs the AI
            # placement of this run, never the composite.
            notes.append(DAMAGED_CLASSIFIER_NOTE)
        else:
            if classifier is None and classifier_file.exists():
                notes.append(STALE_MODEL_NOTE)
    if options.learn:
        # Read now, not after the composite is written: a damaged store must stop the run
        # before any output exists. Learning reads it again; it is small.
        SampleStore(paths.classifier_dir()).load()
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
        # The id is hashed from the bytes read for this decode, so no file is read twice here.
        photo, photo_id = load_photo_with_id(path)
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
    return _Working(number, path, photo_id, image, taken, from_file)


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


def _learn(
    pairs: EmbeddingPair, slots: Sequence[Slot], photo_ids: Sequence[str], model_hash: str
) -> bool:
    """Store the placed photos as confirmed samples and retrain; True once the classifier is."""
    directory = paths.classifier_dir()
    # A random case id and content-hash photo ids keep patient names out of the data folder.
    samples = SampleStore(directory).append(
        pairs,
        slots,
        photo_ids=photo_ids,
        case_id=uuid.uuid4().hex,
        source="export",
        model_hash=model_hash,
        timestamp=time.time(),
    )
    current = samples.for_model(model_hash)
    classifier = train_classifier(current.pairs, current.slots, model_hash)
    if classifier is None:
        # A file left from before (damaged, another model's, or trained on labels since
        # replaced) no longer matches the samples; the next run must agree with "not trained".
        (directory / CLASSIFIER_FILE).unlink(missing_ok=True)
        return False
    classifier.save(directory / CLASSIFIER_FILE)
    return True
