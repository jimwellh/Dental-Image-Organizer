"""File-based Feature 1 batch behind `dio process` (spec §4.10): no library, no server."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Literal

import structlog

from dental_image_organizer.core.align import PreparedReference, prepare_reference
from dental_image_organizer.core.imageio import MAX_JPEG_DIMENSION, RGBImage, encode_jpeg
from dental_image_organizer.core.layout import (
    MAX_SEQUENCE_PIXELS,
    fit_strip_height,
    render_sequence,
    scale_to_height,
)
from dental_image_organizer.core.naming import photo_filename, sanitize_component, sequence_filename
from dental_image_organizer.errors import CorruptImageError, SkipReason, UnsupportedFormatError
from dental_image_organizer.services.batch_io import (
    MTIME_WARNING,
    capture_time,
    load_photo,
    os_error_message,
    unexpected_message,
    write_unique,
)
from dental_image_organizer.services.procedure import (
    compute_edit,
    output_size,
    render_edit,
    render_reference,
)

log = structlog.get_logger(__name__)

OutcomeStatus = Literal["ok", "needs_review", "failed", "skipped"]
Frame = tuple[datetime, RGBImage]


@dataclass(frozen=True)
class PhotoOutcome:
    source: Path
    status: OutcomeStatus
    output: Path | None = None
    confidence: float | None = None
    model: str | None = None
    message: str | None = None
    skip_reason: SkipReason | None = None


@dataclass(frozen=True)
class ProcedureReport:
    reference_output: Path
    photos: list[PhotoOutcome]
    sequence_output: Path

    @property
    def failed(self) -> bool:
        return any(p.status == "failed" for p in self.photos)


def run_procedure(
    reference_path: Path,
    photo_paths: Sequence[Path],
    out_dir: Path,
    *,
    patient: str,
    title: str,
    output_scale: float = 1.0,
    today: date | None = None,
) -> ProcedureReport:
    # Validate names before any work so a bad name cannot leave a half-written export.
    sanitize_component(patient)
    sanitize_component(title)
    # Events are tagged with a position, never a file name: patient folders and re-processed
    # exports carry patient names (spec §4.13). 0 is the reference; photos count from 1.
    with structlog.contextvars.bound_contextvars(photo=0):
        reference = load_photo(reference_path)
        size = output_size(reference.size, output_scale)
        prepared = prepare_reference(reference.rgb)

        out_dir.mkdir(parents=True, exist_ok=True)
        taken = {p.name for p in out_dir.iterdir()}
        reference_time, _ = capture_time(reference, reference_path)
        reference_image = render_reference(reference.rgb, size)
        reference_output = write_unique(
            out_dir,
            taken,
            photo_filename(patient, title, reference_time),
            encode_jpeg(reference_image, reference.metadata),
        )

    # Every output shares `size`, so shrinking each frame as it arrives keeps the frames held for
    # the sequence within the same pixel budget instead of holding every full-size render until
    # the end. The count is the most frames this batch can produce (skips only reduce it).
    strip_height = fit_strip_height(
        [size] * (1 + len(photo_paths)),
        max_width=MAX_JPEG_DIMENSION,
        max_pixels=MAX_SEQUENCE_PIXELS,
    )
    frames: list[Frame] = [(reference_time, scale_to_height(reference_image, strip_height))]
    outcomes: list[PhotoOutcome] = []
    for number, path in enumerate(photo_paths, start=1):
        # Alignment lives in core/, which must not know about photos; binding here tags its
        # debug events with the photo's position.
        with structlog.contextvars.bound_contextvars(photo=number):
            outcome, frame = _process_photo(
                path, prepared, size, strip_height, out_dir, taken, patient=patient, title=title
            )
        outcomes.append(outcome)
        if frame is not None:
            frames.append(frame)

    # Mixing EXIF times that carry an offset with naive file times must not break sorting;
    # the recorded wall-clock time is what orders a single case.
    frames.sort(key=lambda frame: frame[0].replace(tzinfo=None))
    sequence_output = write_unique(
        out_dir,
        taken,
        sequence_filename(patient, title, today or date.today()),
        encode_jpeg(
            render_sequence(
                [image for _, image in frames],
                max_width=MAX_JPEG_DIMENSION,
                max_pixels=MAX_SEQUENCE_PIXELS,
            )
        ),
    )
    return ProcedureReport(reference_output, outcomes, sequence_output)


def _process_photo(
    path: Path,
    prepared: PreparedReference,
    size: tuple[int, int],
    strip_height: int,
    out_dir: Path,
    taken: set[str],
    *,
    patient: str,
    title: str,
) -> tuple[PhotoOutcome, Frame | None]:
    """Align, render, and write one photo; a failure is recorded, never raised (spec §4.13)."""
    try:
        photo = load_photo(path)
    except UnsupportedFormatError as exc:
        return PhotoOutcome(path, "skipped", message=str(exc), skip_reason=exc.reason), None
    except CorruptImageError as exc:
        log.error("photo_unreadable", error=str(exc))
        return PhotoOutcome(path, "failed", message=str(exc)), None
    except OSError as exc:
        log.error("photo_unreadable", error=os_error_message(exc))
        return PhotoOutcome(path, "failed", message=os_error_message(exc)), None
    except Exception as exc:
        # Decoders parse untrusted bytes and hold several full-size copies, so a plugin error
        # or a MemoryError here is as likely as in rendering and must not abort the batch.
        log.exception("photo_unreadable")
        return PhotoOutcome(path, "failed", message=unexpected_message(exc)), None
    try:
        when, from_file = capture_time(photo, path)
        edit = compute_edit(photo.rgb, prepared)
        image = render_edit(photo.rgb, edit, size)
        data = encode_jpeg(image, photo.metadata)
        frame = scale_to_height(image, strip_height)
        # Writing last keeps a photo that fails at any earlier step from leaving a file behind.
        output = write_unique(out_dir, taken, photo_filename(patient, title, when), data)
    except OSError as exc:
        log.error("photo_failed", error=os_error_message(exc))
        return PhotoOutcome(path, "failed", message=os_error_message(exc)), None
    except Exception as exc:
        # One bad photo never aborts a batch: an OpenCV assertion or a MemoryError on a large
        # render fails only this photo. The trace is logged for fixing the cause.
        log.exception("photo_failed")
        return PhotoOutcome(path, "failed", message=unexpected_message(exc)), None
    notes = [m for m in (edit.align.message, MTIME_WARNING if from_file else None) if m]
    outcome = PhotoOutcome(
        path,
        edit.align.status,
        output,
        edit.align.confidence,
        edit.align.model,
        " ".join(notes) or None,
    )
    return outcome, (when, frame)
