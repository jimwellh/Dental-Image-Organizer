"""Feature 2 in the library: slots, cell normalization, and cell edits (spec §3.3, §4.6, §4.7).

Decoding and embedding run outside the case lock. Every change of slots, flips, edits, or layout
then replans the whole composite inside one `update_case`, because colors are fitted across
cells: a new baseline, or a cell that fails, changes the others. A replan reads cached working
copies and holds the case lock for about 0.6 s.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np
import structlog
from structlog.contextvars import bound_contextvars

from dental_image_organizer.config import Settings
from dental_image_organizer.core import transform as tf
from dental_image_organizer.core.geometry import normalize_view
from dental_image_organizer.core.imageio import RGBImage
from dental_image_organizer.core.slots import SLOTS, Flip, Slot
from dental_image_organizer.errors import InvalidRequestError, NotReadyError
from dental_image_organizer.ml.assignment import (
    LOW_CONFIDENCE,
    Assignment,
    AssignMethod,
    assign_by_classifier,
    with_overrides,
)
from dental_image_organizer.ml.classifier import SlotClassifier
from dental_image_organizer.ml.embedder import EmbeddingPair
from dental_image_organizer.services.ai import AssignReason
from dental_image_organizer.services.batch_io import (
    failure_message,
    header_capture_time,
    os_error_message,
)
from dental_image_organizer.services.composite import DEFAULT_BASELINE, fit_cell_colors
from dental_image_organizer.services.flips import effective_flips
from dental_image_organizer.services.intake import Upload, UploadOutcome, add_photos
from dental_image_organizer.services.jobs import JobProgress, JobSnapshot
from dental_image_organizer.services.originals import photo_embedding, working_copy
from dental_image_organizer.services.procedure_case import (
    decode_photo,
    photo_outcome,
    validate_transform,
    with_metadata,
)
from dental_image_organizer.services.shooting_order import OrderedPhoto, shooting_slots
from dental_image_organizer.storage.schema import (
    CELL_SIDE_MAX,
    CELL_SIDE_MIN,
    GAP_COLOR_PATTERN,
    GAP_PX_MAX,
    CaseRecord,
    ColorRecord,
    CompositeRecord,
    EditRecord,
    EditSource,
    PhotoRecord,
)

if TYPE_CHECKING:
    from dental_image_organizer.services.context import AppContext

log = structlog.get_logger(__name__)

COMPOSITE_ALGORITHM = "compose-1"

_PROCEDURE_MESSAGE = "This is a procedure case."
_NOT_PLACED_MESSAGE = "Place the photo in a slot first."
_NOT_PLACEABLE_MESSAGE = "Only processed photos of this case can be placed in a slot."
_HEX_COLOR = re.compile(GAP_COLOR_PATTERN)


@dataclass(frozen=True)
class _Proposal:
    """Slots for the ready members by photo id; a member left out goes to the tray."""

    slots: dict[str, Slot]
    confidences: dict[str, float | None]
    method: AssignMethod
    reason: AssignReason | None

    def summary(self) -> dict[str, Any]:
        return {"method": self.method, "reason": self.reason}


# -- uploads, processing, and assignment ---------------------------------------------------


def upload(ctx: AppContext, case_id: str, uploads: Sequence[Upload]) -> UploadOutcome:
    _read(ctx, case_id)
    added, skipped = add_photos(ctx, case_id, uploads, role="member")
    if not added:
        return UploadOutcome(None, (), tuple(skipped))
    job = submit_processing(ctx, case_id, added)
    return UploadOutcome(job.id, tuple(added), tuple(skipped))


def submit_processing(ctx: AppContext, case_id: str, photo_ids: Sequence[str]) -> JobSnapshot:
    """Queue the `process` job of the photos: after an upload, or for photos a closed or
    crashed run left pending."""
    ids = tuple(photo_ids)

    def run(progress: JobProgress) -> dict[str, Any]:
        return process_photos(ctx, case_id, ids, progress)

    return ctx.jobs.submit("process", run, key=case_id, total=len(ids))


def process_photos(
    ctx: AppContext, case_id: str, photo_ids: Sequence[str], progress: JobProgress
) -> dict[str, Any]:
    """The body of the `process` job (§3.3 steps 2-4); one bad photo fails alone."""
    with bound_contextvars(case_id=case_id):
        wanted = list(dict.fromkeys(photo_ids))
        _read(ctx, case_id)
        for done, photo_id in enumerate(wanted, start=1):
            with bound_contextvars(photo_id=photo_id):
                _process_photo(ctx, case_id, photo_id)
            progress(done, len(wanted))
        case = _read(ctx, case_id)
        # Slots the dentist set by hand are kept: new photos then wait in the tray.
        proposal = _propose(ctx, case) if _block(case).slots_source == "auto" else None
        applied: list[_Proposal] = []

        def change(case: CaseRecord) -> CaseRecord:
            applied.clear()
            # The dentist may have set the slots meanwhile, while the photos were embedded.
            if proposal is not None and _block(case).slots_source == "auto":
                applied.append(proposal)
                return replan(ctx, _placed(case, proposal.slots), confidences=proposal.confidences)
            return replan(ctx, case)

        updated = ctx.library().update_case(case_id, change)
        records = {p.id: p for p in updated.photos}
        processed = [records[i] for i in wanted if i in records]
        log.info(
            "photos_processed",
            processed=len(processed),
            failed=sum(p.state == "failed" for p in processed),
            placed=sum(p.slot is not None for p in updated.photos),
        )
        return {
            "photos": [photo_outcome(p) for p in processed],
            "assignment": applied[0].summary() if applied else None,
        }


def _process_photo(ctx: AppContext, case_id: str, photo_id: str) -> None:
    photo = _find(_read(ctx, case_id), photo_id)
    if photo is None:
        return  # deleted meanwhile
    decoded, error = decode_photo(ctx, case_id, photo)

    def change(case: CaseRecord) -> CaseRecord:
        current = _find(case, photo_id)
        if current is None:
            return case
        if decoded is None:
            # Its cell stays empty, and the shooting order ranks it as a hole.
            failed = {"state": "failed", "error": error, "slot": None, "edit": None}
            return case.replace_photo(current.model_copy(update=failed))
        ready = with_metadata(current, decoded).model_copy(update={"state": "ready", "error": None})
        return case.replace_photo(ready)

    ctx.library().update_case(case_id, change)


def start_auto_assign(ctx: AppContext, case_id: str) -> str:
    """Assign every ready member afresh, discarding manual slots and cell edits; the job id."""
    _read(ctx, case_id)

    def run(progress: JobProgress) -> dict[str, Any]:
        return {"assignment": _auto_assign(ctx, case_id)}

    return ctx.jobs.submit("assign", run, key=case_id).id


def _auto_assign(ctx: AppContext, case_id: str) -> dict[str, Any]:
    with bound_contextvars(case_id=case_id):
        proposal = _propose(ctx, _read(ctx, case_id))

        def change(case: CaseRecord) -> CaseRecord:
            cleared = [p.model_copy(update={"edit": None}) for p in case.photos]
            fresh = _with_source(case.model_copy(update={"photos": cleared}), "auto")
            return replan(ctx, _placed(fresh, proposal.slots), confidences=proposal.confidences)

        ctx.library().update_case(case_id, change)
        log.info("slots_assigned", method=proposal.method, reason=proposal.reason)
        return proposal.summary()


def _propose(ctx: AppContext, case: CaseRecord) -> _Proposal:
    """Slots for every ready member: by the classifier once trained, else by shooting order."""
    ready = [p for p in case.photos if p.state == "ready"]
    classifier, reason = ctx.ai.classifier()
    if classifier is not None:
        assignment, reason = _classify(ctx, case.id, ready, classifier)
        if assignment is not None:
            return _Proposal(
                {p.id: s for p, s in zip(ready, assignment.slots, strict=True) if s is not None},
                dict(zip([p.id for p in ready], assignment.confidences, strict=True)),
                "classifier",
                None,
            )
    # Without a classifier there is no confidence, so none is carried over from an old one.
    return _Proposal(_order_slots(ctx, case), dict.fromkeys(p.id for p in ready), "order", reason)


def _classify(
    ctx: AppContext, case_id: str, ready: Sequence[PhotoRecord], classifier: SlotClassifier
) -> tuple[Assignment | None, AssignReason | None]:
    """The classifier's assignment of `ready`, or why the shooting order has to stand in."""
    if not ready:
        return Assignment((), (), "classifier"), None
    embedder = ctx.ai.embedder()
    if embedder is None:
        return None, "no_model"
    try:
        # Outside the case lock: embedding a photo whose embedding is not cached takes a while.
        pairs = [photo_embedding(ctx, case_id, p, embedder) for p in ready]
    except OSError as exc:
        log.error("embedding_failed", error=os_error_message(exc))
        return None, "embedding_failed"
    except Exception:
        # onnxruntime raises its own exception types, OpenCV its cv2.error, and a large batch
        # a MemoryError; none of them may cost the assignment (spec §4.6).
        log.exception("embedding_failed")
        return None, "embedding_failed"
    joined = EmbeddingPair(
        np.concatenate([p.original for p in pairs]), np.concatenate([p.mirrored for p in pairs])
    )
    return assign_by_classifier(classifier.slot_probabilities(joined)), None


def _order_slots(ctx: AppContext, case: CaseRecord) -> dict[str, Slot]:
    """Shooting order over the ready members; a failed one holds its rank as a hole (§4.6)."""
    library = ctx.library()
    photos: list[OrderedPhoto] = []
    for key, photo in enumerate(case.photos):
        if photo.state == "ready":
            photos.append(OrderedPhoto(key, photo.source_name, photo.captured_at))
        elif photo.state == "failed":
            taken = header_capture_time(library.original_path(case.id, photo))
            photos.append(OrderedPhoto(key, photo.source_name, taken, usable=False))
    slots = shooting_slots(photos, ctx.settings.get().shooting_order)
    return {case.photos[key].id: slot for key, slot in slots.items()}


# -- slots and cell edits ------------------------------------------------------------------


def set_slots(ctx: AppContext, case_id: str, slots: Mapping[Slot, str]) -> CaseRecord:
    """Place exactly the photos of `slots`; every other member goes to the tray."""
    wanted = dict(slots)

    def change(case: CaseRecord) -> CaseRecord:
        _block(case)
        ids = list(wanted.values())
        if len(set(ids)) != len(ids):
            raise InvalidRequestError("A photo can be placed in one slot only.")
        if not set(ids) <= {p.id for p in case.photos if p.state == "ready"}:
            raise InvalidRequestError(_NOT_PLACEABLE_MESSAGE)
        by_photo = {photo_id: slot for slot, photo_id in wanted.items()}
        return replan(ctx, _placed(_with_source(case, "manual"), by_photo))

    with bound_contextvars(case_id=case_id):
        return ctx.library().update_case(case_id, change)


def move_photo(ctx: AppContext, case_id: str, photo_id: str, slot: Slot | None) -> CaseRecord:
    """Drag-to-swap: the photo in `slot` takes the mover's old slot, or the tray."""

    def change(case: CaseRecord) -> CaseRecord:
        _block(case)
        if case.photo(photo_id).state != "ready":
            raise InvalidRequestError(_NOT_PLACEABLE_MESSAGE)
        photos = case.photos
        # with_overrides reads only the slots; the method is carried along unread.
        current = Assignment(tuple(p.slot for p in photos), (None,) * len(photos), "order")
        mover = next(i for i, p in enumerate(photos) if p.id == photo_id)
        moved = with_overrides(current, {mover: slot})
        by_photo = {p.id: s for p, s in zip(photos, moved.slots, strict=True) if s is not None}
        return replan(ctx, _placed(_with_source(case, "manual"), by_photo))

    with bound_contextvars(case_id=case_id, photo_id=photo_id):
        return ctx.library().update_case(case_id, change)


def set_flip(ctx: AppContext, case_id: str, photo_id: str, flip: Flip) -> CaseRecord:
    def change(case: CaseRecord) -> CaseRecord:
        photo, edit = _placed_photo(case, photo_id)
        # The geometry is recomputed from the new flip, so manual pan and zoom are reset.
        flipped = edit.model_copy(update={"flip": flip, "source": "auto"})
        return replan(ctx, case.replace_photo(photo.model_copy(update={"edit": flipped})))

    with bound_contextvars(case_id=case_id, photo_id=photo_id):
        return ctx.library().update_case(case_id, change)


def apply_manual_edit(
    ctx: AppContext, case_id: str, photo_id: str, transform: Sequence[float]
) -> CaseRecord:
    def change(case: CaseRecord) -> CaseRecord:
        photo, edit = _placed_photo(case, photo_id)
        # Without a reference there is no perspective to correct (spec §4.7.2 step 4).
        matrix = validate_transform(transform, "similarity", _block(case).cell_size)
        manual = edit.model_copy(update={"transform": _flat(matrix), "source": "manual"})
        # Replanned rather than stored as is: the cell's colors are fitted to what it now shows.
        return replan(ctx, case.replace_photo(photo.model_copy(update={"edit": manual})))

    with bound_contextvars(case_id=case_id, photo_id=photo_id):
        return ctx.library().update_case(case_id, change)


def reset_edit(ctx: AppContext, case_id: str, photo_id: str) -> CaseRecord:
    """Reset one cell to auto: the slot's default flip and automatic geometry (§4.8)."""

    def change(case: CaseRecord) -> CaseRecord:
        photo, _ = _placed_photo(case, photo_id)
        return replan(ctx, case.replace_photo(photo.model_copy(update={"edit": None})))

    with bound_contextvars(case_id=case_id, photo_id=photo_id):
        return ctx.library().update_case(case_id, change)


def set_layout(ctx: AppContext, case_id: str, layout: CompositeRecord) -> CaseRecord:
    """Change the cell size, gap, and baseline; how the slots were set stays as it is."""

    def change(case: CaseRecord) -> CaseRecord:
        current = _block(case)
        _check_layout(case, layout)
        kept = layout.model_copy(update={"slots_source": current.slots_source})
        return replan(ctx, case.model_copy(update={"composite": kept}))

    with bound_contextvars(case_id=case_id):
        return ctx.library().update_case(case_id, change)


def _check_layout(case: CaseRecord, layout: CompositeRecord) -> None:
    # The block may come from model_copy, which skips the schema's own checks.
    if layout.baseline_photo_id is not None and _find(case, layout.baseline_photo_id) is None:
        raise InvalidRequestError("The baseline photo is not in this case.")
    if not all(CELL_SIDE_MIN <= side <= CELL_SIDE_MAX for side in layout.cell_size):
        raise InvalidRequestError(
            f"Each side of a cell must be {CELL_SIDE_MIN} to {CELL_SIDE_MAX} px."
        )
    if not 0 <= layout.gap_px <= GAP_PX_MAX:
        raise InvalidRequestError(f"The gap must be 0 to {GAP_PX_MAX} px.")
    if not _HEX_COLOR.fullmatch(layout.gap_color):
        raise InvalidRequestError("The gap color must look like #RRGGBB.")


# -- planning ------------------------------------------------------------------------------


def replan(
    ctx: AppContext, case: CaseRecord, *, confidences: Mapping[str, float | None] = {}
) -> CaseRecord:
    """Plan every placed cell again: geometry, then colors across the cells (§4.7).

    Call it inside `update_case`. A manual cell keeps its transform; a photo whose geometry or
    color fit raises fails alone and leaves its cell empty. `confidences` replaces the stored
    confidence of the photos it names.
    """
    cell_size = _block(case).cell_size
    placed = sorted(
        (p for p in case.photos if p.state == "ready" and p.slot is not None),
        key=lambda p: SLOTS.index(p.slot),
    )
    # Read only when needed: a damaged flip history must not block replans that never use it.
    defaults = composite_flips(ctx) if any(_flip(p) is None for p in placed) else {}
    images: list[RGBImage] = []
    slot_map: dict[Slot, int] = {}
    transforms: dict[Slot, tf.Matrix] = {}
    labels: list[str] = []
    flips: dict[str, Flip] = {}
    failures: dict[str, str] = {}
    for photo in placed:
        slot = _slot(photo)
        flip = _flip(photo) or defaults[slot]
        with bound_contextvars(photo_id=photo.id):
            try:
                # Cached since processing. A miss decodes the full original under the case
                # lock, which is slow but only happens after the cache dropped it.
                image = working_copy(ctx, case.id, photo)
                if photo.edit is not None and photo.edit.source == "manual":
                    transform = photo.edit.matrix()
                else:
                    transform = normalize_view(image, slot, flip, cell_size).transform
            except OSError as exc:
                failures[photo.id] = os_error_message(exc)
                log.error("photo_failed", error=failures[photo.id])
                continue
            except Exception as exc:
                # A photo that breaks leveling or cropping leaves only its cell empty; it never
                # aborts the composite (spec §4.13). The trace is for the cause.
                log.exception("photo_failed")
                failures[photo.id] = failure_message(exc)
                continue
        slot_map[slot] = len(images)
        images.append(image)
        labels.append(photo.id)
        transforms[slot] = transform
        flips[photo.id] = flip

    baseline, colors, color_failed = fit_cell_colors(
        images, slot_map, transforms, _baseline(case), cell_size, labels, label_key="photo_id"
    )
    failures |= {labels[index]: failure_message(exc) for index, exc in color_failed.items()}

    photos: list[PhotoRecord] = []
    for photo in case.photos:
        update: dict[str, Any]
        if photo.id in failures:
            update = {"state": "failed", "error": failures[photo.id], "slot": None, "edit": None}
        elif photo.id in flips:
            slot = _slot(photo)
            if slot == baseline:
                color = ColorRecord(method="identity")
            else:
                color = ColorRecord(method="lab_reinhard_v1", params=colors[slot].to_params())
            if photo.id in confidences:
                confidence = confidences[photo.id]
            else:
                confidence = photo.edit.confidence if photo.edit is not None else None
            low = confidence is not None and confidence < LOW_CONFIDENCE
            edit = EditRecord(
                transform=_flat(transforms[slot]),
                model="similarity",
                output_size=cell_size,
                color=color,
                source=photo.edit.source if photo.edit is not None else "auto",
                confidence=confidence,
                status="needs_review" if low else "ok",
                message=None,
                algorithm_version=COMPOSITE_ALGORITHM,
                flip=flips[photo.id],
            )
            update = {"edit": edit}
        else:
            # In the tray, or not processed: nothing to render.
            update = {"edit": None}
        photos.append(photo.model_copy(update=update))
    return case.model_copy(update={"photos": photos})


def composite_flips(ctx: AppContext) -> dict[Slot, Flip]:
    """The flip each slot starts from: its pin, else the learned flip, else the start value."""
    # flip_history reads under the AI lock: on Windows a read racing a write of flips.json fails.
    return effective_flips(ctx.settings.get(), ctx.ai.flip_history())


def composite_block(settings: Settings) -> CompositeRecord:
    """The layout of a new composite case."""
    return CompositeRecord(
        baseline_photo_id=None,
        cell_size=settings.cell_size,
        gap_px=settings.gap_px,
        gap_color=settings.gap_color,
        slots_source="auto",
    )


def _placed(case: CaseRecord, slots: Mapping[str, Slot]) -> CaseRecord:
    """The case with `slots` as its placement, by photo id; any other member goes to the tray."""
    photos: list[PhotoRecord] = []
    for photo in case.photos:
        slot = slots.get(photo.id) if photo.state == "ready" else None
        if slot != photo.slot:
            # Its next plan starts from the new slot's default flip and automatic geometry.
            photo = photo.model_copy(update={"slot": slot, "edit": None})
        photos.append(photo)
    return case.model_copy(update={"photos": photos})


def _baseline(case: CaseRecord) -> Slot:
    chosen = _block(case).baseline_photo_id
    photo = _find(case, chosen) if chosen is not None else None
    if photo is None or photo.state != "ready" or photo.slot is None:
        return DEFAULT_BASELINE
    return photo.slot


# -- records -------------------------------------------------------------------------------


def _read(ctx: AppContext, case_id: str) -> CaseRecord:
    case = ctx.library().read_case(case_id)
    _block(case)
    return case


def _block(case: CaseRecord) -> CompositeRecord:
    # The schema gives every composite case a block, and no procedure case one.
    if case.type != "composite" or case.composite is None:
        raise InvalidRequestError(_PROCEDURE_MESSAGE)
    return case.composite


def _with_source(case: CaseRecord, source: EditSource) -> CaseRecord:
    block = _block(case).model_copy(update={"slots_source": source})
    return case.model_copy(update={"composite": block})


def _placed_photo(case: CaseRecord, photo_id: str) -> tuple[PhotoRecord, EditRecord]:
    _block(case)
    photo = case.photo(photo_id)
    # A replan gives every placed, ready photo an edit, or fails it and empties its slot.
    if photo.state != "ready" or photo.slot is None or photo.edit is None:
        raise NotReadyError(_NOT_PLACED_MESSAGE)
    return photo, photo.edit


def _find(case: CaseRecord, photo_id: str) -> PhotoRecord | None:
    return next((p for p in case.photos if p.id == photo_id), None)


def _slot(photo: PhotoRecord) -> Slot:
    # Only placed photos are planned.
    assert photo.slot is not None
    return photo.slot


def _flip(photo: PhotoRecord) -> Flip | None:
    return photo.edit.flip if photo.edit is not None else None


def _flat(matrix: tf.Matrix) -> list[float]:
    return [float(v) for v in matrix.ravel()]
