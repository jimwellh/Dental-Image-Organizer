from typer.testing import CliRunner

from dental_image_organizer import __version__
from dental_image_organizer.cli import app


def test_version_flag_prints_package_version() -> None:
    result = CliRunner().invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == __version__
