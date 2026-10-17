from dental_image_organizer import paths


def test_app_folders_are_per_user_and_nested() -> None:
    assert paths.classifier_dir() == paths.data_dir() / "classifier"
    assert paths.model_cache_dir() == paths.cache_dir() / "models"
    assert "DentalImageOrganizer" in str(paths.data_dir())
