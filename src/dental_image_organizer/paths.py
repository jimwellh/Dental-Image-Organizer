"""Per-user app folders. Kept out of `core` and `ml` so those layers stay free of OS state."""

import os
from dataclasses import dataclass
from pathlib import Path

import platformdirs

APP_NAME = "DentalImageOrganizer"
# Tests and the macOS smoke test set this so they never touch the real per-user folders.
APP_DIR_ENV = "DIO_APP_DIR"


def _override(name: str) -> Path | None:
    root = os.environ.get(APP_DIR_ENV)
    return Path(root) / name if root else None


def config_dir() -> Path:
    return _override("config") or Path(platformdirs.user_config_dir(APP_NAME, appauthor=False))


def data_dir() -> Path:
    return _override("data") or Path(platformdirs.user_data_dir(APP_NAME, appauthor=False))


def cache_dir() -> Path:
    return _override("cache") or Path(platformdirs.user_cache_dir(APP_NAME, appauthor=False))


def log_dir() -> Path:
    return _override("log") or Path(platformdirs.user_log_dir(APP_NAME, appauthor=False))


def classifier_dir() -> Path:
    return data_dir() / "classifier"


def model_cache_dir() -> Path:
    return cache_dir() / "models"


@dataclass(frozen=True)
class AppDirs:
    """The four app folders plus the files and subfolders every layer agrees on."""

    config: Path
    data: Path
    cache: Path
    log: Path

    @property
    def settings_file(self) -> Path:
        return self.config / "settings.json"

    @property
    def launcher_file(self) -> Path:
        return self.config / "launcher.json"

    @property
    def classifier(self) -> Path:
        return self.data / "classifier"

    @property
    def instance_lock(self) -> Path:
        return self.data / "instance.lock"

    @property
    def instance_file(self) -> Path:
        return self.data / "instance.json"

    @property
    def models(self) -> Path:
        return self.cache / "models"

    @property
    def renders(self) -> Path:
        return self.cache / "renders"

    @property
    def exports(self) -> Path:
        return self.cache / "exports"

    @property
    def teach(self) -> Path:
        return self.cache / "teach"

    @property
    def update_check_file(self) -> Path:
        return self.cache / "update-check.json"

    @property
    def update_script(self) -> Path:
        return self.cache / "update.sh"

    @property
    def update_result_file(self) -> Path:
        return self.cache / "update-result.json"

    @property
    def log_file(self) -> Path:
        return self.log / "dio.log"

    @property
    def update_log_file(self) -> Path:
        return self.log / "update.log"


def app_dirs() -> AppDirs:
    # Built at call time so tests that monkeypatch the individual functions still take effect.
    return AppDirs(config=config_dir(), data=data_dir(), cache=cache_dir(), log=log_dir())
