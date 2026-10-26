"""The macOS launcher bundle (spec §7.3), with `osacompile` and `codesign` faked so it runs
anywhere; the macOS smoke test builds and launches a real one."""

from __future__ import annotations

import plistlib
from collections.abc import Sequence
from pathlib import Path, PurePosixPath

import pytest
from PIL import Image

from dental_image_organizer.errors import LauncherError
from dental_image_organizer.launcher import macos
from dental_image_organizer.launcher.macos import (
    APP_NAME,
    BUNDLE_ID,
    applet_lines,
    bundle_path,
    install_launcher,
    uninstall_launcher,
)

# POSIX paths, as on the Mac, whatever system runs the tests.
DIO = PurePosixPath("/Users/alex/.local/share/uv/tools/dental-image-organizer/bin/dio")


class FakeTools:
    """Builds what osacompile builds, as far as the launcher touches it."""

    def __init__(self, *, fail: str | None = None) -> None:
        self.calls: list[list[str]] = []
        self.fail = fail

    def __call__(self, args: Sequence[str]) -> None:
        self.calls.append(list(args))
        if args[0] == self.fail:
            raise LauncherError(f"{args[0]} failed: boom")
        if args[0] == "osacompile":
            bundle = Path(args[args.index("-o") + 1])
            (bundle / "Contents" / "MacOS").mkdir(parents=True)
            (bundle / "Contents" / "MacOS" / "applet").write_bytes(b"stub")
            (bundle / "Contents" / "Resources").mkdir()
            (bundle / "Contents" / "Resources" / "applet.icns").write_bytes(b"old icon")
            info = {
                "CFBundleIdentifier": "com.apple.ScriptEditor.id.Dental-Image-Organizer",
                "CFBundleIconFile": "applet",
                "CFBundleIconName": "applet",
                "CFBundleExecutable": "applet",
            }
            with (bundle / "Contents" / "Info.plist").open("wb") as handle:
                plistlib.dump(info, handle)

    def script(self) -> list[str]:
        compile_args = next(call for call in self.calls if call[0] == "osacompile")
        return [compile_args[i + 1] for i, arg in enumerate(compile_args) if arg == "-e"]


def _info(app: Path) -> dict[str, object]:
    with (app / "Contents" / "Info.plist").open("rb") as handle:
        info: dict[str, object] = plistlib.load(handle)
    return info


def _foreign_app(applications: Path) -> Path:
    app = bundle_path(applications)
    (app / "Contents").mkdir(parents=True)
    with (app / "Contents" / "Info.plist").open("wb") as handle:
        plistlib.dump({"CFBundleIdentifier": "com.example.other"}, handle)
    return app


def test_the_applet_starts_the_server_by_its_absolute_path() -> None:
    lines = applet_lines(DIO, None)

    assert lines[0] == "try"
    assert lines[1] == f'\tdo shell script "{DIO} serve --detach"'
    assert lines[-1] == "end try"
    assert "could not start" in lines[3]


def test_the_applet_quotes_for_the_shell_and_for_applescript() -> None:
    odd = PurePosixPath('/Users/Dr O\'Neil "Al"/bin/dio')

    line = applet_lines(odd, None)[1]

    # shlex quoting for sh, then each " and \ escaped for the AppleScript string literal.
    shell = """'/Users/Dr O'"'"'Neil "Al"/bin/dio' serve --detach"""
    assert line == '\tdo shell script "' + shell.replace('"', '\\"') + '"'


def test_a_separate_app_folder_is_kept_by_the_applet() -> None:
    line = applet_lines(DIO, "/tmp/smoke app")[1]

    assert line == f"\tdo shell script \"DIO_APP_DIR='/tmp/smoke app' {DIO} serve --detach\""


def test_install_builds_signs_and_places_the_bundle(tmp_path: Path) -> None:
    tools = FakeTools()
    applications = tmp_path / "Applications"

    app = install_launcher(dio_path=DIO, applications_dir=applications, app_dir=None, run=tools)

    assert app == applications / f"{APP_NAME}.app"
    assert (app / "Contents" / "MacOS" / "applet").read_bytes() == b"stub"
    assert [call[0] for call in tools.calls] == ["osacompile", "codesign"]
    assert tools.calls[1][:4] == ["codesign", "--force", "--sign", "-"]
    assert tools.script() == applet_lines(DIO, None)
    info = _info(app)
    assert info["CFBundleIdentifier"] == BUNDLE_ID
    assert info["CFBundleName"] == APP_NAME
    assert info["CFBundleIconFile"] == "applet"
    assert "CFBundleIconName" not in info
    with Image.open(app / "Contents" / "Resources" / "applet.icns") as icon:
        assert icon.format == "ICNS"
        assert icon.size == (1024, 1024)
    # Nothing is left beside the app.
    assert [p.name for p in applications.iterdir()] == [app.name]


def test_install_again_replaces_its_own_bundle(tmp_path: Path) -> None:
    applications = tmp_path / "Applications"
    install_launcher(dio_path=DIO, applications_dir=applications, app_dir=None, run=FakeTools())
    other = PurePosixPath("/opt/dio")

    tools = FakeTools()
    install_launcher(dio_path=other, applications_dir=applications, app_dir=None, run=tools)

    assert tools.script() == applet_lines(other, None)
    assert [p.name for p in applications.iterdir()] == [f"{APP_NAME}.app"]


def test_a_failed_build_keeps_the_previous_launcher(tmp_path: Path) -> None:
    applications = tmp_path / "Applications"
    app = install_launcher(
        dio_path=DIO, applications_dir=applications, app_dir=None, run=FakeTools()
    )

    with pytest.raises(LauncherError, match="codesign failed"):
        install_launcher(
            dio_path=DIO,
            applications_dir=applications,
            app_dir=None,
            run=FakeTools(fail="codesign"),
        )

    assert _info(app)["CFBundleIdentifier"] == BUNDLE_ID
    assert [p.name for p in applications.iterdir()] == [app.name]


def test_install_never_replaces_an_app_it_did_not_make(tmp_path: Path) -> None:
    applications = tmp_path / "Applications"
    foreign = _foreign_app(applications)
    tools = FakeTools()

    with pytest.raises(LauncherError, match="was not made by dio install-launcher"):
        install_launcher(dio_path=DIO, applications_dir=applications, app_dir=None, run=tools)

    assert tools.calls == []
    assert _info(foreign)["CFBundleIdentifier"] == "com.example.other"


def test_uninstall_removes_only_its_own_bundle(tmp_path: Path) -> None:
    applications = tmp_path / "Applications"
    app = install_launcher(
        dio_path=DIO, applications_dir=applications, app_dir=None, run=FakeTools()
    )

    assert uninstall_launcher(applications) == app
    assert not app.exists()
    assert uninstall_launcher(applications) is None

    foreign = _foreign_app(applications)
    with pytest.raises(LauncherError):
        uninstall_launcher(applications)
    assert foreign.exists()


def test_the_dio_beside_the_interpreter_comes_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "dio").write_text("")
    monkeypatch.setattr(macos.sys, "executable", str(bin_dir / "python"))

    assert macos.default_dio_path() == bin_dir / "dio"


def test_without_a_dio_the_launcher_cannot_be_made(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(macos.sys, "executable", str(tmp_path / "python"))
    monkeypatch.setattr(macos.shutil, "which", lambda name: None)

    with pytest.raises(LauncherError, match="dio command was not found"):
        macos.default_dio_path()


def test_the_icon_is_a_white_tooth_on_an_opaque_tile() -> None:
    icon = macos.icon_image()

    assert icon.size == (1024, 1024)
    assert icon.getpixel((0, 0)) == (0, 0, 0, 0)
    # The middle of the crown is white, and the tile around it is opaque teal.
    assert icon.getpixel((512, 420)) == (255, 255, 255, 255)
    red, green, _blue, alpha = icon.getpixel((150, 512))  # type: ignore[misc]
    assert alpha == 255 and green > red
