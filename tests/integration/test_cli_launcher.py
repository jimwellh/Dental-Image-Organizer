"""`dio install-launcher` and `dio uninstall-launcher` (spec §4.10, §7.3)."""

from __future__ import annotations

import plistlib
from collections.abc import Sequence
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dental_image_organizer.cli import app
from dental_image_organizer.config import read_launcher
from dental_image_organizer.launcher import macos
from dental_image_organizer.paths import app_dirs


class FakeTools:
    def __init__(self) -> None:
        self.scripts: list[list[str]] = []

    def __call__(self, args: Sequence[str]) -> None:
        if args[0] != "osacompile":
            return
        self.scripts.append([args[i + 1] for i, arg in enumerate(args) if arg == "-e"])
        bundle = Path(args[args.index("-o") + 1])
        (bundle / "Contents" / "Resources").mkdir(parents=True)
        with (bundle / "Contents" / "Info.plist").open("wb") as handle:
            plistlib.dump({"CFBundleIdentifier": "com.apple.ScriptEditor.id.x"}, handle)


@pytest.fixture
def mac(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeTools:
    """A pretend Mac: the commands run, with the bundle tools faked."""
    monkeypatch.setenv("DIO_APP_DIR", str(tmp_path / "app"))
    bin_dir = tmp_path / "tool" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "dio").write_text("")
    monkeypatch.setattr(macos, "supported", lambda: True)
    monkeypatch.setattr(macos.sys, "executable", str(bin_dir / "python"))
    tools = FakeTools()
    monkeypatch.setattr(macos, "_run", tools)
    return tools


def test_install_creates_the_app_and_records_the_paths(mac: FakeTools, tmp_path: Path) -> None:
    uv = tmp_path / "uv"
    uv.write_text("")
    applications = tmp_path / "Applications"

    result = CliRunner().invoke(
        app, ["install-launcher", "--uv-path", str(uv), "--applications-dir", str(applications)]
    )

    assert result.exit_code == 0, result.output
    bundle = applications / "Dental Image Organizer.app"
    assert f"Created {bundle}" in result.output
    assert bundle.is_dir()
    record = read_launcher(app_dirs().launcher_file)
    assert record is not None
    assert record.uv_path == uv
    assert record.dio_path == tmp_path / "tool" / "bin" / "dio"
    # The launcher made under DIO_APP_DIR keeps using it.
    assert "DIO_APP_DIR=" in mac.scripts[0][1]


def test_install_without_uv_says_how_to_update(
    mac: FakeTools, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("dental_image_organizer.cli.shutil.which", lambda name: None)

    result = CliRunner().invoke(
        app, ["install-launcher", "--applications-dir", str(tmp_path / "Applications")]
    )

    assert result.exit_code == 0, result.output
    assert "uv tool upgrade dental-image-organizer" in result.output
    record = read_launcher(app_dirs().launcher_file)
    assert record is not None and record.uv_path is None


def test_install_rejects_a_missing_uv(mac: FakeTools, tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "install-launcher",
            "--uv-path",
            str(tmp_path / "nope"),
            "--applications-dir",
            str(tmp_path / "Applications"),
        ],
    )

    assert result.exit_code == 2
    assert not (tmp_path / "Applications").exists()


def test_install_refuses_to_replace_another_app(mac: FakeTools, tmp_path: Path) -> None:
    bundle = tmp_path / "Applications" / "Dental Image Organizer.app"
    (bundle / "Contents").mkdir(parents=True)
    with (bundle / "Contents" / "Info.plist").open("wb") as handle:
        plistlib.dump({"CFBundleIdentifier": "com.example.other"}, handle)

    result = CliRunner().invoke(
        app, ["install-launcher", "--applications-dir", str(tmp_path / "Applications")]
    )

    assert result.exit_code == 1
    assert "was not made by dio install-launcher" in result.output
    assert read_launcher(app_dirs().launcher_file) is None


def test_uninstall_removes_the_app_and_the_record(mac: FakeTools, tmp_path: Path) -> None:
    applications = tmp_path / "Applications"
    runner = CliRunner()
    runner.invoke(app, ["install-launcher", "--applications-dir", str(applications)])

    result = runner.invoke(app, ["uninstall-launcher", "--applications-dir", str(applications)])

    assert result.exit_code == 0, result.output
    assert "Removed" in result.output
    assert not (applications / "Dental Image Organizer.app").exists()
    assert not app_dirs().launcher_file.exists()
    again = runner.invoke(app, ["uninstall-launcher", "--applications-dir", str(applications)])
    assert "No launcher was installed." in again.output


@pytest.mark.parametrize("command", ["install-launcher", "uninstall-launcher"])
def test_other_systems_are_told_it_is_not_supported(
    command: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DIO_APP_DIR", str(tmp_path / "app"))
    monkeypatch.setattr(macos, "supported", lambda: False)

    result = CliRunner().invoke(app, [command])

    assert result.exit_code == 1
    assert "not supported" in result.output
