"""Per-user app folders. Kept out of `core` and `ml` so those layers stay free of OS state."""

from pathlib import Path

import platformdirs

APP_NAME = "DentalImageOrganizer"


def data_dir() -> Path:
    return Path(platformdirs.user_data_dir(APP_NAME, appauthor=False))


def cache_dir() -> Path:
    return Path(platformdirs.user_cache_dir(APP_NAME, appauthor=False))


def classifier_dir() -> Path:
    return data_dir() / "classifier"


def model_cache_dir() -> Path:
    return cache_dir() / "models"
