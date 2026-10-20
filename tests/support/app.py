"""An app context over temporary folders, plus the photo sets the service and API tests share."""

from __future__ import annotations

import io
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import numpy as np
from PIL import ExifTags, Image

from dental_image_organizer.config import Settings, SettingsStore
from dental_image_organizer.core import transform as tf
from dental_image_organizer.core.geometry import flip_pixels
from dental_image_organizer.core.slots import DEFAULT_FLIPS, SLOTS
from dental_image_organizer.ml.model_store import DEFAULT_MODEL, ModelSpec
from dental_image_organizer.paths import AppDirs
from dental_image_organizer.services.context import AppContext, create_context
from dental_image_organizer.services.intake import Upload
from dental_image_organizer.services.jobs import JobSnapshot
from support.scenarios import about_center, make_pair
from support.synthetic import make_scene
from support.views import make_view

ORIGIN = "http://127.0.0.1:8765"
WAIT_SECONDS = 120.0
SCENE_SIZE = (2400, 1600)


def make_context(
    root: Path,
    *,
    model_spec: ModelSpec | None = None,
    http_client: Callable[[], httpx.Client] | None = None,
    **settings: Any,
) -> AppContext:
    dirs = AppDirs(root / "config", root / "data", root / "cache", root / "log")
    # model_validate, not model_copy: the overrides must pass the same checks as a saved file.
    values = {"library_root": root / "library", **settings}
    SettingsStore(dirs.settings_file).put(Settings.model_validate(values))
    return create_context(
        dirs, model_spec=model_spec or DEFAULT_MODEL, workers=2, http_client=http_client
    )


def jpeg(rgb: np.ndarray, taken: str | None = None, *, gps: bool = False) -> bytes:
    exif = Image.Exif()
    if taken:
        exif.get_ifd(ExifTags.IFD.Exif)[ExifTags.Base.DateTimeOriginal] = taken
    if gps:
        location = exif.get_ifd(ExifTags.IFD.GPSInfo)
        location[ExifTags.GPS.GPSLatitudeRef] = "N"
        location[ExifTags.GPS.GPSLatitude] = (25.0, 2.0, 30.0)
    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, format="JPEG", quality=92, exif=exif.tobytes())
    return buf.getvalue()


def upload(name: str, data: bytes, last_modified: datetime | None = None) -> Upload:
    return Upload(name, io.BytesIO(data), last_modified)


def wait(ctx: AppContext, job_id: str) -> JobSnapshot:
    snapshot = ctx.jobs.wait(job_id, timeout=WAIT_SECONDS)
    assert snapshot.state == "done", snapshot
    return snapshot


def view_set(seed: int = 70, count: int = 9) -> list[tuple[str, bytes]]:
    """One composite session shot in row-major slot order, as the camera stored it."""
    # The tenth photo is a second shot of the first slot, which the tray must take.
    shots = [(slot, seed + i) for i, slot in enumerate(SLOTS)] + [(SLOTS[0], seed + 50)]
    if not 1 <= count <= len(shots):
        raise ValueError(f"view_set builds 1 to {len(shots)} photos, not {count}")
    files = []
    for i, (slot, view_seed) in enumerate(shots[:count]):
        view = flip_pixels(make_view(slot, view_seed, angle=3.0), DEFAULT_FLIPS[slot])
        files.append((f"IMG_{i + 1:04d}.jpg", jpeg(view, f"2026:10:16 10:00:{10 + i:02d}")))
    return files


@dataclass(frozen=True)
class ProcedureSet:
    reference: bytes
    intra: bytes
    post: bytes
    truth: tuple[float, ...]  # intra photo -> reference, normalized as an edit record stores it


def procedure_set(
    seed: int = 61, *, reference_scale: float = 1.0, gps: bool = False
) -> ProcedureSet:
    width, height = SCENE_SIZE
    scene = make_scene(seed, width, height)
    intra = make_pair(scene, about_center(width, height, 1.0, 4.0), reference_scale=reference_scale)
    post = make_pair(scene, about_center(width, height, 0.9, -6.0))
    truth = tf.to_normalized(intra.truth, intra.photo.shape[1], intra.reference.shape[1])
    return ProcedureSet(
        reference=jpeg(intra.reference, "2026:10:16 09:00:00", gps=gps),
        intra=jpeg(intra.photo, "2026:10:16 10:00:00", gps=gps),
        post=jpeg(post.photo, "2026:10:20 10:00:00", gps=gps),
        truth=tuple(float(v) for v in truth.ravel()),
    )
