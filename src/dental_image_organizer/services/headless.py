"""File-based Feature 1 batch behind `dio process` (spec §4.10): no library, no server."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Literal

import structlog

from dental_image_organizer.core.align import prepare_reference
from dental_image_organizer.core.imageio import DecodedImage, RGBImage, decode, encode_jpeg
from dental_image_organizer.core.layout import render_sequence
from dental_image_organizer.core.naming import (
    photo_filename,
    sanitize_component,
    sequence_filename,
    unique_name,
)
from dental_image_organizer.errors import CorruptImageError, SkipReason, UnsupportedFormatError
from dental_image_organizer.services.procedure import (
    compute_edit,
    output_size,
    render_edit,
    render_reference,
)

log = structlog.get_logger(__name__)

OutcomeStatus = Literal["ok", "needs_review", "failed", "skipped"]
MTIME_WARNING = "No capture time in EXIF; used the file modification time."


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
    reference = _load(reference_path)
    size = output_size(reference.size, output_scale)
    prepared = prepare_reference(reference.rgb)

    out_dir.mkdir(parents=True, exist_ok=True)
    taken = {p.name for p in out_dir.iterdir()}
    reference_time, _ = _captured_at(reference, reference_path)
    reference_image = render_reference(reference.rgb, size)
    reference_output = _write(
        out_dir,
        taken,
        photo_filename(patient, title, reference_time),
        encode_jpeg(reference_image, reference.metadata),
    )

    frames: list[tuple[datetime, RGBImage]] = [(reference_time, reference_image)]
    outcomes: list[PhotoOutcome] = []
    for path in photo_paths:
        try:
            photo = _load(path)
        except UnsupportedFormatError as exc:
            outcomes.append(PhotoOutcome(path, "skipped", message=str(exc), skip_reason=exc.reason))
            continue
        except (CorruptImageError, OSError) as exc:
            log.error("photo_unreadable", source=path.name, error=str(exc))
            outcomes.append(PhotoOutcome(path, "failed", message=str(exc)))
            continue
        edit = compute_edit(photo.rgb, prepared)
        image = render_edit(photo.rgb, edit, size)
        when, from_file = _captured_at(photo, path)
        output = _write(
            out_dir, taken, photo_filename(patient, title, when), encode_jpeg(image, photo.metadata)
        )
        frames.append((when, image))
        notes = [m for m in (edit.align.message, MTIME_WARNING if from_file else None) if m]
        outcomes.append(
            PhotoOutcome(
                path,
                edit.align.status,
                output,
                edit.align.confidence,
                edit.align.model,
                " ".join(notes) or None,
            )
        )

    # Mixing EXIF times that carry an offset with naive file times must not break sorting;
    # the recorded wall-clock time is what orders a single case.
    frames.sort(key=lambda frame: frame[0].replace(tzinfo=None))
    sequence_output = _write(
        out_dir,
        taken,
        sequence_filename(patient, title, today or date.today()),
        encode_jpeg(render_sequence([image for _, image in frames])),
    )
    return ProcedureReport(reference_output, outcomes, sequence_output)


def _load(path: Path) -> DecodedImage:
    return decode(path.read_bytes())


def _captured_at(image: DecodedImage, path: Path) -> tuple[datetime, bool]:
    if image.metadata.captured_at is not None:
        return image.metadata.captured_at, False
    log.warning("capture_time_from_file", source=path.name)
    return datetime.fromtimestamp(path.stat().st_mtime), True


def _write(out_dir: Path, taken: set[str], name: str, data: bytes) -> Path:
    final = unique_name(name, taken)
    taken.add(final)
    path = out_dir / final
    path.write_bytes(data)
    return path
