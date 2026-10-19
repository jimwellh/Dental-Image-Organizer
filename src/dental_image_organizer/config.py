"""User settings (spec §4.11), stored as `settings.json` in the app's config folder."""

import json
import os
import re
import threading
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from dental_image_organizer.core.slots import DEFAULT_FLIPS, SLOTS, Flip, Slot
from dental_image_organizer.errors import StorageError
from dental_image_organizer.paths import APP_DIR_ENV
from dental_image_organizer.storage.atomic import write_atomic
from dental_image_organizer.storage.schema import (
    CELL_SIDE_MAX,
    CELL_SIDE_MIN,
    GAP_COLOR_PATTERN,
    GAP_PX_MAX,
)

_HEX_COLOR = re.compile(GAP_COLOR_PATTERN)


def default_library_root() -> Path:
    # Read at call time so tests can redirect it with the environment or Path.home.
    root = os.environ.get(APP_DIR_ENV)
    return Path(root) / "library" if root else Path.home() / "DentalImageOrganizer"


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    library_root: Path = Field(default_factory=default_library_root)
    jpeg_quality: int = Field(default=92, ge=50, le=100)
    strip_gps: bool = True
    cell_size: tuple[int, int] = (1200, 800)
    gap_px: int = Field(default=0, ge=0, le=GAP_PX_MAX)
    gap_color: str = "#000000"
    shooting_order: tuple[Slot, ...] = SLOTS
    flip_start: dict[Slot, Flip] = Field(default_factory=lambda: dict(DEFAULT_FLIPS))
    flip_pins: dict[Slot, Flip] = Field(default_factory=dict)
    cache_max_gb: float = Field(default=2.0, gt=0, le=100)
    idle_shutdown_minutes: int = Field(default=30, ge=0, le=1440)

    @field_validator("library_root")
    @classmethod
    def _absolute(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("library_root must be an absolute path")
        return value

    @field_validator("cell_size")
    @classmethod
    def _cell_size_range(cls, value: tuple[int, int]) -> tuple[int, int]:
        if not all(CELL_SIDE_MIN <= side <= CELL_SIDE_MAX for side in value):
            raise ValueError("each side of cell_size must be 100-4000")
        return value

    @field_validator("gap_color")
    @classmethod
    def _gap_color_format(cls, value: str) -> str:
        if not _HEX_COLOR.fullmatch(value):
            raise ValueError("gap_color must look like #RRGGBB")
        return value

    @field_validator("shooting_order")
    @classmethod
    def _every_slot_once(cls, value: tuple[Slot, ...]) -> tuple[Slot, ...]:
        if sorted(value) != sorted(SLOTS):
            raise ValueError("shooting_order must list every slot exactly once")
        return value

    @field_validator("flip_start")
    @classmethod
    def _fill_flip_start(cls, value: dict[Slot, Flip]) -> dict[Slot, Flip]:
        # A slot the file leaves out keeps its built-in default.
        return DEFAULT_FLIPS | value


class SettingsStore:
    """Loads once at construction; `put` writes atomically and swaps the in-memory copy."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._settings = self._load()

    def get(self) -> Settings:
        with self._lock:
            return self._settings

    def put(self, settings: Settings) -> Settings:
        write_atomic(self._path, settings.model_dump_json(indent=2).encode("utf-8"))
        with self._lock:
            self._settings = settings
        return settings

    def _load(self) -> Settings:
        if not self._path.exists():
            return Settings()
        damaged = StorageError(
            f"The settings file is damaged; move {self._path} aside to start with default settings."
        )
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise damaged from exc
        version = raw.get("schema_version") if isinstance(raw, dict) else None
        if isinstance(version, int) and version > 1:
            raise StorageError(
                "The settings were saved by a newer version of Dental Image Organizer; "
                "update the app."
            )
        try:
            return Settings.model_validate(raw)
        except ValidationError as exc:
            raise damaged from exc
