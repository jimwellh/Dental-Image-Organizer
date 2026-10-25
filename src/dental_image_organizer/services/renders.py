"""JPEG renders of photos, sequences, and composites, cached by content (spec §4.8).

These functions run in whatever thread calls them; the API runs them through `run_cpu`. They read
the case once and render from that snapshot, so no case lock is held while pixels are computed.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Literal

import cv2
import structlog

from dental_image_organizer.core.imageio import (
    MAX_JPEG_DIMENSION,
    RGBImage,
    encode_jpeg,
    resize_long_edge,
)
from dental_image_organizer.core.layout import (
    MAX_SEQUENCE_PIXELS,
    fit_strip_height,
    render_sequence,
    scale_to_height,
)
from dental_image_organizer.core.render import render
from dental_image_organizer.core.slots import Slot
from dental_image_organizer.errors import NotReadyError
from dental_image_organizer.services.composite import compose
from dental_image_organizer.services.originals import decode_original, working_copy
from dental_image_organizer.services.procedure_case import sequence_photos
from dental_image_organizer.storage.cache import cache_key
from dental_image_organizer.storage.schema import CaseRecord, EditRecord, PhotoRecord

if TYPE_CHECKING:
    from dental_image_organizer.services.context import AppContext

log = structlog.get_logger(__name__)

THUMB_EDGE = 320
PREVIEW_EDGE = 1600
PREVIEW_QUALITY = 85

ImageVariant = Literal["original", "processed"]
ImageSize = Literal["thumb", "preview", "full"]
RenderSize = Literal["preview", "full"]


def photo_image(
    ctx: AppContext, case_id: str, photo_id: str, *, variant: ImageVariant, size: ImageSize
) -> bytes:
    photo = ctx.library().read_case(case_id).photo(photo_id)
    edit = _processed_edit(photo) if variant == "processed" else None
    quality = _quality(ctx, size)
    # An original does not depend on the edit, so editing never evicts it from the cache.
    pixels = edit.render_key() if edit is not None else ""
    key = cache_key("photo", variant, size, photo.sha256, pixels, str(quality), photo.role)

    def produce() -> bytes:
        if edit is None:
            rgb = _original(ctx, case_id, photo, size)
        else:
            rgb = _processed(ctx, case_id, photo, edit, size)
        return encode_jpeg(rgb, None, quality=quality)

    return _cached(ctx, key, produce)


def sequence_image(ctx: AppContext, case_id: str, *, size: RenderSize) -> bytes:
    case = ctx.library().read_case(case_id)
    frames = _sequence_frames(case)
    quality = _quality(ctx, size)
    gap_px = _sequence_gap(case)
    key = cache_key(
        "sequence",
        size,
        str(gap_px),
        str(quality),
        *(p.sha256 + _edit(p).render_key() for p in frames),
    )
    return _cached(
        ctx,
        key,
        lambda: encode_jpeg(
            _strip(ctx, case.id, frames, gap_px, full=size == "full"), quality=quality
        ),
    )


def composite_image(ctx: AppContext, case_id: str, *, size: RenderSize) -> bytes:
    case = ctx.library().read_case(case_id)
    cells = _placed_cells(case)
    block = case.composite
    assert block is not None
    quality = _quality(ctx, size)
    key = cache_key(
        "composite",
        size,
        f"{block.cell_size[0]}x{block.cell_size[1]}",
        str(block.gap_px),
        block.gap_color,
        str(quality),
        *(slot + p.sha256 + _edit(p).render_key() for slot, p in cells),
    )
    return _cached(
        ctx,
        key,
        lambda: encode_jpeg(_grid(ctx, case, cells, full=size == "full"), quality=quality),
    )


def render_full(photo: PhotoRecord, rgb: RGBImage) -> RGBImage:
    """Render a photo's edit at its own output size from a full decode."""
    edit = _edit(photo)
    return _render_edit(photo, edit, rgb, edit.output_size, cv2.INTER_LANCZOS4)


def sequence_strip(ctx: AppContext, case: CaseRecord, *, full: bool) -> RGBImage:
    return _strip(ctx, case.id, _sequence_frames(case), _sequence_gap(case), full=full)


def composite_grid(ctx: AppContext, case: CaseRecord, *, full: bool) -> RGBImage:
    return _grid(ctx, case, _placed_cells(case), full=full)


def fit_long_edge(size: tuple[int, int], long_edge: int) -> tuple[int, int]:
    """Scale (width, height) so the longer side is `long_edge`, with the rounding of
    `resize_long_edge`; a smaller size is returned as is."""
    scale = long_edge / max(size)
    if scale >= 1.0:
        return size
    return max(1, round(size[0] * scale)), max(1, round(size[1] * scale))


# -- photos --------------------------------------------------------------------------------


def _original(ctx: AppContext, case_id: str, photo: PhotoRecord, size: ImageSize) -> RGBImage:
    if size == "full":
        return decode_original(ctx, case_id, photo).rgb
    working = working_copy(ctx, case_id, photo)
    if size == "preview":
        return working
    thumb, _ = resize_long_edge(working, THUMB_EDGE)
    return thumb


def _processed(
    ctx: AppContext, case_id: str, photo: PhotoRecord, edit: EditRecord, size: ImageSize
) -> RGBImage:
    if size == "full":
        return render_full(photo, decode_original(ctx, case_id, photo).rgb)
    if size == "thumb":
        # A thumbnail needs no more detail than the working copy holds.
        source = working_copy(ctx, case_id, photo)
        target = fit_long_edge(edit.output_size, THUMB_EDGE)
    else:
        # A tightly cropped edit leaves few working-copy pixels in the frame, and the compare
        # view must show real detail, so the preview renders from the full original.
        source = decode_original(ctx, case_id, photo).rgb
        target = fit_long_edge(edit.output_size, PREVIEW_EDGE)
    return _render_edit(photo, edit, source, target, cv2.INTER_LINEAR)


def _render_edit(
    photo: PhotoRecord,
    edit: EditRecord,
    rgb: RGBImage,
    size: tuple[int, int],
    interpolation: int,
) -> RGBImage:
    # Replicating the edge keeps black from bleeding in where a reference is enlarged (see
    # services.procedure.render_reference).
    border = cv2.BORDER_REPLICATE if photo.role == "reference" else cv2.BORDER_CONSTANT
    return render(
        rgb,
        edit.matrix(),
        size,
        color=edit.color_transform(),
        interpolation=interpolation,
        border_mode=border,
    )


def _processed_edit(photo: PhotoRecord) -> EditRecord:
    if photo.state != "ready" or photo.edit is None:
        raise NotReadyError("This photo has not been processed yet.")
    return photo.edit


def _edit(photo: PhotoRecord) -> EditRecord:
    # Callers pass photos that were selected for having an edit.
    if photo.edit is None:
        raise NotReadyError("This photo has not been processed yet.")
    return photo.edit


# -- sequence and composite ----------------------------------------------------------------


def _sequence_frames(case: CaseRecord) -> list[PhotoRecord]:
    frames = sequence_photos(case)
    if not frames:
        raise NotReadyError("The case has no processed photos yet.")
    return frames


def _sequence_gap(case: CaseRecord) -> int:
    return case.sequence.gap_px if case.sequence is not None else 0


def _strip(
    ctx: AppContext, case_id: str, frames: list[PhotoRecord], gap_px: int, *, full: bool
) -> RGBImage:
    sizes = [_edit(p).output_size for p in frames]
    max_width = MAX_JPEG_DIMENSION if full else PREVIEW_EDGE
    height = fit_strip_height(
        sizes, gap_px=gap_px, max_width=max_width, max_pixels=MAX_SEQUENCE_PIXELS
    )
    images: list[RGBImage] = []
    for photo, (width, frame_height) in zip(frames, sizes, strict=True):
        # Each frame shrinks as it arrives, so the strip never holds every full-size render.
        if full:
            image = render_full(photo, decode_original(ctx, case_id, photo).rgb)
            images.append(scale_to_height(image, height))
        else:
            target = (max(1, round(width * height / frame_height)), height)
            source = working_copy(ctx, case_id, photo)
            images.append(_render_edit(photo, _edit(photo), source, target, cv2.INTER_LINEAR))
    return render_sequence(
        images, gap_px=gap_px, max_width=max_width, max_pixels=MAX_SEQUENCE_PIXELS
    )


def _placed_cells(case: CaseRecord) -> list[tuple[Slot, PhotoRecord]]:
    cells = [
        (p.slot, p)
        for p in case.photos
        if p.slot is not None and p.state == "ready" and p.edit is not None
    ]
    if not cells:
        raise NotReadyError("No photo is placed yet.")
    return cells


def _grid(
    ctx: AppContext, case: CaseRecord, cells: list[tuple[Slot, PhotoRecord]], *, full: bool
) -> RGBImage:
    block = case.composite
    assert block is not None
    rendered: dict[Slot, RGBImage] = {}
    for slot, photo in cells:
        edit = _edit(photo)
        if full:
            source, interpolation = decode_original(ctx, case.id, photo).rgb, cv2.INTER_LANCZOS4
        else:
            source, interpolation = working_copy(ctx, case.id, photo), cv2.INTER_LINEAR
        rendered[slot] = _render_edit(photo, edit, source, block.cell_size, interpolation)
    grid = compose(rendered, block.cell_size, gap_px=block.gap_px, gap_color=_rgb(block.gap_color))
    if full:
        return grid
    preview, _ = resize_long_edge(grid, PREVIEW_EDGE)
    return preview


def _rgb(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


# -- cache ---------------------------------------------------------------------------------


def _quality(ctx: AppContext, size: ImageSize) -> int:
    return ctx.settings.get().jpeg_quality if size == "full" else PREVIEW_QUALITY


def _cached(ctx: AppContext, key: str, produce: Callable[[], bytes]) -> bytes:
    data = ctx.cache.get(key)
    if data is not None:
        return data
    data = produce()
    try:
        ctx.cache.put(key, data)
    except OSError as exc:
        # The cache only saves time; a full or locked cache folder must not fail the render.
        log.warning("cache_write_failed", error=exc.strerror or type(exc).__name__)
    return data
