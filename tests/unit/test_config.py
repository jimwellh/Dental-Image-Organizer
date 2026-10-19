from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from dental_image_organizer.config import Settings, SettingsStore
from dental_image_organizer.core.slots import DEFAULT_FLIPS, SLOTS
from dental_image_organizer.errors import StorageError


def test_defaults_match_the_spec(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DIO_APP_DIR", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    s = Settings()

    assert s.library_root == tmp_path / "DentalImageOrganizer"
    assert s.jpeg_quality == 92
    assert s.strip_gps is True
    assert s.cell_size == (1200, 800)
    assert s.gap_px == 0
    assert s.shooting_order == SLOTS
    assert s.flip_start == DEFAULT_FLIPS
    assert s.flip_pins == {}
    assert s.cache_max_gb == 2.0
    assert s.idle_shutdown_minutes == 30


def test_missing_file_gives_defaults_and_writes_nothing(tmp_path: Path) -> None:
    p = tmp_path / "config" / "settings.json"

    assert SettingsStore(p).get() == Settings()
    assert not p.exists()


def test_settings_round_trip(tmp_path: Path) -> None:
    p = tmp_path / "settings.json"
    store = SettingsStore(p)
    changed = store.get().model_copy(
        update={"jpeg_quality": 80, "flip_pins": {"upper_left": "none"}}
    )

    assert store.put(changed) == changed
    assert SettingsStore(p).get() == changed


@pytest.mark.parametrize(
    "overrides",
    [
        {"library_root": "relative/dir"},
        {"shooting_order": (*SLOTS[:-1], SLOTS[0])},
        {"jpeg_quality": 101},
        {"gap_color": "red"},
        {"cell_size": (50, 800)},
        {"unknown_field": 1},
    ],
)
def test_invalid_settings_are_rejected(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Settings(**overrides)


def test_partial_flip_start_keeps_the_other_defaults() -> None:
    s = Settings(flip_start={"upper_right": "none"})

    assert s.flip_start == DEFAULT_FLIPS | {"upper_right": "none"}


def test_damaged_settings_file_is_a_storage_error(tmp_path: Path) -> None:
    p = tmp_path / "settings.json"
    p.write_text("{", encoding="utf-8")

    with pytest.raises(StorageError, match="move"):
        SettingsStore(p)


def test_settings_from_a_newer_version_ask_for_an_update(tmp_path: Path) -> None:
    p = tmp_path / "settings.json"
    p.write_text('{"schema_version": 2}', encoding="utf-8")

    with pytest.raises(StorageError, match="newer version"):
        SettingsStore(p)
