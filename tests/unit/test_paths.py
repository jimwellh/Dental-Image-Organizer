from pathlib import Path

import pytest

from dental_image_organizer import config, paths


def test_app_folders_are_per_user_and_nested() -> None:
    assert paths.classifier_dir() == paths.data_dir() / "classifier"
    assert paths.model_cache_dir() == paths.cache_dir() / "models"
    assert "DentalImageOrganizer" in str(paths.data_dir())


def test_app_dir_env_puts_every_folder_under_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(paths.APP_DIR_ENV, str(tmp_path))

    dirs = paths.app_dirs()

    assert dirs == paths.AppDirs(
        tmp_path / "config", tmp_path / "data", tmp_path / "cache", tmp_path / "log"
    )
    assert dirs.settings_file == tmp_path / "config" / "settings.json"
    assert dirs.log_file == tmp_path / "log" / "dio.log"
    assert dirs.instance_lock == tmp_path / "data" / "instance.lock"
    assert dirs.instance_file == tmp_path / "data" / "instance.json"
    assert dirs.renders == tmp_path / "cache" / "renders"
    assert dirs.exports == tmp_path / "cache" / "exports"
    assert dirs.teach == tmp_path / "cache" / "teach"
    assert config.default_library_root() == tmp_path / "library"


def test_app_dirs_follow_the_platform_folders(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(paths.APP_DIR_ENV, raising=False)

    dirs = paths.app_dirs()

    assert dirs.data == paths.data_dir()
    assert dirs.classifier == paths.classifier_dir()
    assert dirs.models == paths.model_cache_dir()
