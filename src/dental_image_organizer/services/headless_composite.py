"""File-based Feature 2 batch behind `dio composite` (spec §3.3, §4.6, §4.10)."""

from __future__ import annotations

import os
import re
import time
import unicodedata
import uuid
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
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
    assign_by_classifier,
    check_overrides,
    shooting_rank,
    with_overrides,
)
from dental_image_organizer.ml.classifier import (
    MIN_SAMPLES_PER_SLOT,
    SlotClassifier,
    load_classifier,
    train_classifier,
)
from dental_image_organizer.ml.embedder import Embedder, EmbeddingPair
from dental_image_organizer.ml.model_store import DEFAULT_MODEL, cached_model, download_model
from dental_image_organizer.ml.samples import SampleStore
from dental_image_organizer.services.batch_io import (
    MTIME_WARNING,
    capture_time,
    header_capture_time,
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
    compose,
    plan_composite,
    render_cell,
)
from dental_image_organizer.services.headless import PhotoOutcome

log = structlog.get_logger(__name__)

WORKING_LONG_EDGE = 1600
CLASSIFIER_FILE = "model.json"

ORDER_NOTE = (
    "Slots were assigned by shooting order: the AI classifier needs "
    f"{MIN_SAMPLES_PER_SLOT} confirmed samples per slot."
)
ORDER_HINT = "Check the result, fix it with --assign, and rerun with --learn to teach it."
NO_MODEL_NOTE = (
    "The AI classifier is trained, but the AI model is not downloaded; slots were assigned by "
    "shooting order. Rerun with --download-model."
)
ORDER_FALLBACK_NOTE = "Slots were assigned by shooting order."
STALE_MODEL_NOTE = (
    "The AI model changed since the classifier was trained, so its samples no longer apply; "
    f"confirm {MIN_SAMPLES_PER_SLOT} sets again with --learn. Slots were assigned by shooting "
    "order."
)
DAMAGED_CLASSIFIER_NOTE = (
    "The AI classifier file is damaged and was ignored; slots were assigned by shooting order. "
    "Rerun with --learn to rebuild it."
)
LEARN_NEEDS_MODEL = (
    "--learn needs the AI model (downloaded once from github.com); rerun with --download-model."
)
DUPLICATE_PHOTO_NOTE = (
    "The same photo file was placed in more than one slot, so those cells were not learned."
)
NO_PHOTOS = "No usable photos to compose."


def _ignore(_message: str) -> None:
    return None


@dataclass(frozen=True)
class CompositeOptions:
    """`assign` maps a photo, by its index in the input, to a slot or None (tray).

    `resolve_assign` turns file names or paths into these indices.
    """

    order: Sequence[Slot] = SLOTS
    flips: Mapping[Slot, Flip] = field(default_factory=lambda: dict(DEFAULT_FLIPS))
    baseline: Slot = DEFAULT_BASELINE
    assign: Mapping[int, Slot | None] = field(default_factory=dict)
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
    `learn_error` says why storing the samples of a --learn run failed after the composite was
    written.
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
    learn_error: str | None = None

    @property
    def failed(self) -> bool:
        return self.learn_error is not None or any(p.status == "failed" for p in self.problems)


@dataclass(frozen=True)
class _AiState:
    classifier: SlotClassifier | None
    embedder: Embedder | None
    notes: tuple[str, ...]


@dataclass(frozen=True)
class _Working:
    """A decoded photo kept as a small working copy.

    `number` is its 1-based input position; `photo_id` is the SHA-256 of its file, hashed only
    for a --learn run.
    """

    number: int
    source: Path
    photo_id: str | None
    image: RGBImage
    taken: datetime
    from_file: bool


@dataclass(frozen=True)
class _Hole:
    """An input that failed to decode; `taken` is the capture time left in its header."""

    number: int
    source: Path
    taken: datetime | None


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
    if sorted(options.order) != sorted(SLOTS):
        raise ValueError("order must list every slot exactly once")
    check_overrides(options.assign, len(photo_paths))
    # The AI state comes before decoding so a --learn without the model fails before writing.
    ai = _resolve_ai(options, notify)

    problems: dict[int, PhotoOutcome] = {}
    workings: list[_Working] = []
    for number, path in enumerate(photo_paths, start=1):
        # Events are tagged with a position, never a file name: patient folders and exports
        # carry patient names (spec §4.13).
        with structlog.contextvars.bound_contextvars(photo=number):
            loaded = _load_working(number, path, with_id=options.learn)
        if isinstance(loaded, PhotoOutcome):
            problems[number] = loaded
        else:
            workings.append(loaded)
    if not workings:
        raise DioError(NO_PHOTOS)

    images = [w.image for w in workings]
    embeddings, embed_notes = _embed(ai.embedder, images, learn=options.learn)
    if ai.classifier is not None and embeddings is not None:
        # An unreadable photo has no embedding, so the classifier simply never places it.
        assignment = assign_by_classifier(ai.classifier.slot_probabilities(embeddings))
    else:
        holes = [
            _Hole(number, p.source, header_capture_time(p.source))
            for number, p in problems.items()
            if p.status == "failed"
        ]
        assignment = _order_keeping_holes(workings, holes, options.order)
    # An --assign entry for a photo that failed to decode is ignored: it is reported already.
    index = {w.number - 1: i for i, w in enumerate(workings)}
    assignment = with_overrides(
        assignment,
        {index[photo]: slot for photo, slot in options.assign.items() if photo in index},
    )
    notes = [*ai.notes, *embed_notes]
    if assignment.method == "order" and not notes:
        # A --learn run is the one that teaches it.
        notes.append(ORDER_NOTE if options.learn else f"{ORDER_NOTE} {ORDER_HINT}")
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

    learned, trained, learn_error = 0, False, None
    if options.learn and rendered:
        # _resolve_ai and _embed give --learn an embedder and embeddings, or raise.
        assert ai.embedder is not None and embeddings is not None
        placed = [(slot, plan.cells[slot].photo) for slot in rendered]
        counts = Counter(_photo_id(workings[photo]) for _, photo in placed)
        # Identical files in two slots would give one photo id two contradicting labels, and
        # neither can be told right.
        kept = [(slot, photo) for slot, photo in placed if counts[_photo_id(workings[photo])] == 1]
        if len(kept) < len(placed):
            notes.append(DUPLICATE_PHOTO_NOTE)
        try:
            trained = _learn(
                embeddings.take([photo for _, photo in kept]),
                [slot for slot, _ in kept],
                [_photo_id(workings[photo]) for _, photo in kept],
                ai.embedder.model_hash,
            )
        except Exception as exc:
            # The composite is written already; a failed sample write must not hide it.
            log.exception("learning_failed")
            learn_error = _failure_message(exc)
        else:
            learned = len(kept)

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
        learn_error=learn_error,
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


def resolve_assign(
    entries: Iterable[tuple[str, Slot | None]], photo_paths: Sequence[Path]
) -> dict[int, Slot | None]:
    """`CompositeOptions.assign` from (file name or path, slot or None) entries.

    Raises ValueError for a name that matches no input, a photo named twice, or two photos
    in one slot.
    """
    resolved: dict[int, Slot | None] = {}
    for name, slot in entries:
        photo = find_photo(name, photo_paths)
        if photo in resolved:
            raise ValueError(f"{name!r} is assigned twice")
        if slot is not None and slot in resolved.values():
            raise ValueError(f"two files are assigned to {slot}")
        resolved[photo] = slot
    return resolved


def _order_keeping_holes(
    workings: Sequence[_Working], holes: Sequence[_Hole], order: Sequence[Slot]
) -> Assignment:
    """Shooting order in which each unreadable photo still holds its rank, leaving it empty.

    Without the hole, every later photo would move up one slot. Photos may be passed in any
    order, so a hole ranks by the capture time left in its header; without one, it ranks right
    after the photo whose file name precedes it, because cameras number files in shooting
    order. Equal capture times rank by file name for the same reason. A hole without a capture
    time still ranks wrong when the camera's file numbers wrap past 9999 next to it.
    """
    # Listed by file name, so shooting_rank breaks a tie of capture times by file name.
    timed = sorted(
        [(w.taken, w.number, w.source) for w in workings]
        + [(h.taken, h.number, h.source) for h in holes if h.taken is not None],
        key=lambda entry: (_name_key(entry[2]), entry[1]),
    )
    ranked = [(timed[i][1], timed[i][2]) for i in shooting_rank([e[0] for e in timed])]
    untimed = sorted(
        (h for h in holes if h.taken is None), key=lambda h: (_name_key(h.source), h.number)
    )
    for hole in untimed:
        ranked.insert(_name_position(ranked, hole.number, hole.source), (hole.number, hole.source))
    by_number = {number: slot for (number, _), slot in zip(ranked, order, strict=False)}
    slots = tuple(by_number.get(w.number) for w in workings)
    return Assignment(slots, (None,) * len(slots), "order")


def _name_position(ranked: Sequence[tuple[int, Path]], number: int, source: Path) -> int:
    """The rank right after the photo whose file name precedes `source`'s."""
    # Input order settles equal names, such as a retake saved in another folder.
    key = (_name_key(source), number)
    keys = [(_name_key(path), n) for n, path in ranked]
    earlier = [i for i, other in enumerate(keys) if other < key]
    if earlier:
        return max(earlier, key=lambda i: keys[i]) + 1
    # No file name precedes it, so it was shot before the photo whose name comes first.
    return min(range(len(keys)), key=lambda i: keys[i])


def _name_key(path: Path) -> tuple[str | int, ...]:
    # Numbers compare as numbers, so IMG_99 sorts before IMG_100; macOS may hand over
    # decomposed (NFD) names.
    name = unicodedata.normalize("NFC", path.name).casefold()
    parts = re.split(r"(\d+)", name)
    return tuple(int(part) if i % 2 else part for i, part in enumerate(parts))


def _resolve_ai(options: CompositeOptions, notify: Callable[[str], None]) -> _AiState:
    classifier_file = paths.classifier_dir() / CLASSIFIER_FILE
    if not (options.learn or classifier_file.exists()):
        return _AiState(None, None, ())
    if options.learn:
        # Read now, not after the composite is written: a damaged store must stop the run
        # before any output exists. Learning reads it again; it is small.
        SampleStore(paths.classifier_dir()).load()
    try:
        return _load_ai(classifier_file, options, notify)
    except (ModelUnavailableError, ModelIntegrityError) as exc:
        if options.learn:
            raise
        # A model that cannot be fetched, read, or loaded must not block the composite
        # (spec §4.6).
        return _AiState(None, None, (_fallback_note(str(exc)),))


def _load_ai(
    classifier_file: Path, options: CompositeOptions, notify: Callable[[str], None]
) -> _AiState:
    # Read at call time, not import time, so a test can swap in the fake model.
    spec = DEFAULT_MODEL
    model_path = cached_model(spec, paths.model_cache_dir())
    if model_path is None and options.download_model:
        notify(f"Downloading the AI model ({spec.size_bytes // 1_000_000} MB) from github.com ...")
        model_path = download_model(spec, paths.model_cache_dir())
    if model_path is None:
        if options.learn:
            raise ModelUnavailableError(LEARN_NEEDS_MODEL)
        return _AiState(None, None, (NO_MODEL_NOTE,))

    notes: list[str] = []
    classifier: SlotClassifier | None = None
    try:
        classifier = load_classifier(classifier_file, spec.sha256)
    except StorageError:
        # The classifier is rebuilt from the samples, so a damaged file only costs the AI
        # placement of this run, never the composite.
        notes.append(DAMAGED_CLASSIFIER_NOTE)
    else:
        if classifier is None and classifier_file.exists():
            notes.append(STALE_MODEL_NOTE)
    if classifier is None and not options.learn:
        return _AiState(None, None, tuple(notes))
    return _AiState(classifier, Embedder(model_path, spec.sha256), tuple(notes))


def _embed(
    embedder: Embedder | None, images: Sequence[RGBImage], *, learn: bool
) -> tuple[EmbeddingPair | None, tuple[str, ...]]:
    """Embeddings and the notes of a failure; a failure only raises when learning needs them."""
    if embedder is None:
        return None, ()
    try:
        return embedder.embed(images), ()
    except Exception as exc:
        # onnxruntime raises its own exception types, OpenCV its cv2.error, and a large batch
        # a MemoryError; none of them may cost the composite (spec §4.6).
        log.exception("embedding_failed")
        message = f"Running the AI model failed: {_failure_message(exc)}"
        if learn:
            raise ModelUnavailableError(message) from exc
        return None, (_fallback_note(message),)


def _load_working(number: int, path: Path, *, with_id: bool) -> _Working | PhotoOutcome:
    """Decode one photo into a working copy; a failure is recorded, never raised (spec §4.13).

    Only the working copy outlives this call, so one full-size photo is in memory at a time.
    """
    photo_id: str | None = None
    try:
        if with_id:
            # The id is hashed from the bytes read for this decode, so no file is read twice.
            photo, photo_id = load_photo_with_id(path)
        else:
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


def _fallback_note(reason: str) -> str:
    # Our error messages carry no closing period, while runtime messages (onnxruntime, an
    # unexpected exception) often do; either way the note must read as two sentences.
    reason = reason.rstrip()
    stop = "" if reason.endswith((".", "!", "?")) else "."
    return f"{reason}{stop} {ORDER_FALLBACK_NOTE}"


def _failure_message(exc: Exception) -> str:
    # The caller has logged the trace already; this only words the reported message.
    if isinstance(exc, DioError):
        return str(exc)
    return os_error_message(exc) if isinstance(exc, OSError) else unexpected_message(exc)


def _photo_id(working: _Working) -> str:
    # _load_working hashes every photo of a --learn run, the only run that reads the ids.
    assert working.photo_id is not None
    return working.photo_id


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
