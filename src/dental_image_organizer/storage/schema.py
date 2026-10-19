"""Versioned records of `patient.json` and `case.json` (spec §4.8, §4.11)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal, Self

import numpy as np
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    PositiveInt,
    ValidationError,
    field_validator,
    model_validator,
)

from dental_image_organizer.core import transform as tf
from dental_image_organizer.core.align import AlignModel
from dental_image_organizer.core.color import LabAffine, LabCurves, LabTransform
from dental_image_organizer.core.imageio import MAX_JPEG_DIMENSION, MAX_PIXELS
from dental_image_organizer.core.slots import Flip, Slot
from dental_image_organizer.errors import PhotoNotFoundError, StorageError

SCHEMA_VERSION = 1
OUTPUT_SCALES = (1.0, 1.5, 2.0)
ID_PATTERN = r"^[0-9a-f]{32}$"
SHA256_PATTERN = r"^[0-9a-f]{64}$"
# Anchored to the originals folder with a fixed-width name, so a hand-edited or imported record
# can never point outside the case folder.
ORIGINAL_FILE_PATTERN = r"^originals/[0-9a-f]{16}\.(heic|jpg|png)$"
EXPORT_FILE_PATTERN = r"^export/[^/\\]+$"
# Layout bounds, shared with config.Settings: a hand-edited or imported record must not be able
# to make a later render allocate an enormous canvas.
CELL_SIDE_MIN = 100
CELL_SIDE_MAX = 4000
GAP_PX_MAX = 200
GAP_COLOR_PATTERN = r"^#[0-9A-Fa-f]{6}$"

CaseType = Literal["procedure", "composite"]
Role = Literal["reference", "photo", "member"]
Stage = Literal["pre_op", "intra_op", "post_op"]
PhotoState = Literal["pending", "ready", "failed"]
EditStatus = Literal["ok", "needs_review"]
EditSource = Literal["auto", "manual"]
CaptureSource = Literal["exif", "file_mtime"]
ColorMethod = Literal["identity", "lab_quantile_v1", "lab_reinhard_v1"]
ExportKind = Literal["single", "sequence", "composite"]

Id = Annotated[str, Field(pattern=ID_PATTERN)]
Size = tuple[PositiveInt, PositiveInt]
CellSize = tuple[
    Annotated[int, Field(ge=CELL_SIDE_MIN, le=CELL_SIDE_MAX)],
    Annotated[int, Field(ge=CELL_SIDE_MIN, le=CELL_SIDE_MAX)],
]


def new_id() -> str:
    return uuid.uuid4().hex


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PatientRecord(_Record):
    schema_version: Literal[1] = 1
    id: Id
    name: str = Field(min_length=1)
    note: str | None = None
    created_at: AwareDatetime


class ColorRecord(_Record):
    method: ColorMethod
    params: dict[str, Any] = {}


class EditRecord(_Record):
    transform: list[Annotated[float, Field(allow_inf_nan=False)]] = Field(
        min_length=9, max_length=9
    )
    model: AlignModel
    output_size: Size
    detail_ratio: float | None = None
    color: ColorRecord
    source: EditSource = "auto"
    confidence: float | None = None
    status: EditStatus
    message: str | None = None
    algorithm_version: str
    flip: Flip | None = None

    @field_validator("output_size")
    @classmethod
    def _output_size_limits(cls, value: tuple[int, int]) -> tuple[int, int]:
        # Same limits the live pipeline enforces (services.procedure.output_size).
        if max(value) > MAX_JPEG_DIMENSION or value[0] * value[1] > MAX_PIXELS:
            raise ValueError("output_size exceeds the supported image size")
        return value

    def matrix(self) -> tf.Matrix:
        return np.asarray(self.transform, dtype=np.float64).reshape(3, 3)

    def color_transform(self) -> LabTransform | None:
        match self.color.method:
            case "identity":
                return None
            case "lab_quantile_v1":
                return LabCurves.from_params(self.color.params)
            case "lab_reinhard_v1":
                return LabAffine.from_params(self.color.params)

    def render_key(self) -> str:
        """Canonical JSON of the fields that change pixels; status and provenance are left out."""
        pixels = {
            "transform": self.transform,
            "output_size": list(self.output_size),
            "color": self.color.model_dump(mode="json"),
        }
        return json.dumps(pixels, sort_keys=True, separators=(",", ":"))


class PhotoRecord(_Record):
    id: Id
    sha256: str = Field(pattern=SHA256_PATTERN)
    file: str = Field(pattern=ORIGINAL_FILE_PATTERN)
    source_name: str
    width: int | None = None
    height: int | None = None
    captured_at: datetime | None = None
    captured_at_source: CaptureSource | None = None
    role: Role
    stage: Stage | None = None
    stage_source: EditSource = "auto"
    slot: Slot | None = None
    state: PhotoState = "pending"
    error: str | None = None
    edit: EditRecord | None = None


class SequenceRecord(_Record):
    photo_ids: list[Id] = []
    gap_px: int = Field(default=0, ge=0, le=GAP_PX_MAX)


class CompositeRecord(_Record):
    baseline_photo_id: Id | None
    cell_size: CellSize
    gap_px: int = Field(ge=0, le=GAP_PX_MAX)
    gap_color: str = Field(pattern=GAP_COLOR_PATTERN)
    slots_source: EditSource


class ExportRecord(_Record):
    file: str = Field(pattern=EXPORT_FILE_PATTERN)
    kind: ExportKind
    created_at: AwareDatetime

    @field_validator("file")
    @classmethod
    def _not_a_dot_name(cls, value: str) -> str:
        # The pattern accepts "export/..", which would name the case folder itself.
        if value.removeprefix("export/") in {".", ".."}:
            raise ValueError("export file name must not be a relative path component")
        return value


class CaseRecord(_Record):
    schema_version: Literal[1] = 1
    id: Id
    type: CaseType
    patient_id: Id
    title: str
    output_scale: float | None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    photos: list[PhotoRecord] = []
    sequence: SequenceRecord | None
    composite: CompositeRecord | None
    exports: list[ExportRecord] = []

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        ids = [p.id for p in self.photos]
        if len(set(ids)) != len(ids):
            raise ValueError("photo ids must be unique")
        if self.type == "procedure":
            self._check_procedure(set(ids))
        else:
            self._check_composite(set(ids))
        if self.type != "composite" and any(p.edit and p.edit.flip for p in self.photos):
            raise ValueError("only composite cells carry a flip")
        return self

    def _check_procedure(self, ids: set[str]) -> None:
        if self.output_scale not in OUTPUT_SCALES:
            raise ValueError(f"output_scale must be one of {OUTPUT_SCALES}")
        if self.sequence is None or self.composite is not None:
            raise ValueError("a procedure case has a sequence and no composite block")
        if not set(self.sequence.photo_ids) <= ids:
            raise ValueError("sequence names a photo that is not in the case")
        if any(p.role == "member" for p in self.photos):
            raise ValueError("a procedure case has no member photos")
        if sum(p.role == "reference" for p in self.photos) > 1:
            raise ValueError("a procedure case has at most one reference")
        if any(p.slot is not None for p in self.photos):
            raise ValueError("a procedure photo has no slot")

    def _check_composite(self, ids: set[str]) -> None:
        if self.output_scale is not None:
            raise ValueError("a composite case has no output_scale")
        if self.composite is None or self.sequence is not None:
            raise ValueError("a composite case has a composite block and no sequence")
        if self.composite.baseline_photo_id not in ids | {None}:
            raise ValueError("baseline_photo_id is not a photo of the case")
        if any(p.role != "member" for p in self.photos):
            raise ValueError("a composite case holds member photos only")
        if any(p.stage is not None for p in self.photos):
            raise ValueError("a composite photo has no stage")
        slots = [p.slot for p in self.photos if p.slot is not None]
        if len(set(slots)) != len(slots):
            raise ValueError("each slot holds at most one photo")

    def photo(self, photo_id: str) -> PhotoRecord:
        for photo in self.photos:
            if photo.id == photo_id:
                return photo
        raise PhotoNotFoundError("This photo is no longer in the case.")

    def reference(self) -> PhotoRecord | None:
        return next((p for p in self.photos if p.role == "reference"), None)

    def replace_photo(self, photo: PhotoRecord) -> CaseRecord:
        self.photo(photo.id)
        photos = [photo if p.id == photo.id else p for p in self.photos]
        return self.model_copy(update={"photos": photos})

    def validated(self) -> CaseRecord:
        """Re-run validation: `model_copy(update=...)` skips it, so every write calls this."""
        return CaseRecord.model_validate(self.model_dump())


def _load[T: _Record](model: type[T], data: bytes) -> T:
    try:
        raw = json.loads(data)
    except ValueError as exc:
        raise StorageError("The file is damaged: it is not valid JSON.") from exc
    if not isinstance(raw, dict):
        raise StorageError("The file is damaged: it does not hold a JSON object.")
    version = raw.get("schema_version")
    if isinstance(version, int) and not isinstance(version, bool) and version > SCHEMA_VERSION:
        raise StorageError(
            "This file was saved by a newer version of Dental Image Organizer; "
            "update the app to open it."
        )
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first["loc"]) or "(record)"
        raise StorageError(f"The file is damaged: {where}: {first['msg']}") from exc


def parse_patient(data: bytes) -> PatientRecord:
    return _load(PatientRecord, data)


def parse_case(data: bytes) -> CaseRecord:
    return _load(CaseRecord, data)


def dump(record: BaseModel) -> bytes:
    return record.model_dump_json(indent=2).encode()
