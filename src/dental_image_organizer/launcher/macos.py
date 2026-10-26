"""The macOS launcher (spec §7.3): an AppleScript applet in `~/Applications` that starts the
server in the background and opens the app in the browser.

The bundle is built on the user's Mac, so it carries no quarantine attribute and Gatekeeper
does not prompt; no signing identity or Apple account is involved.
"""

from __future__ import annotations

import os
import plistlib
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path, PurePath

from packaging.version import Version
from PIL import Image, ImageDraw

from dental_image_organizer import __version__
from dental_image_organizer.errors import LauncherError
from dental_image_organizer.paths import APP_DIR_ENV

APP_NAME = "Dental Image Organizer"
BUNDLE_ID = "io.github.jimwellh.dental-image-organizer"
ICON_SIZE = 1024
# The icon is drawn this many times larger, then scaled down, which smooths its edges.
_SUPERSAMPLE = 2
_BEZIER_STEPS = 32

Runner = Callable[[Sequence[str]], None]

# The tooth outline on the 1024 px icon grid, as cubic Bézier segments (start, control,
# control, end), clockwise from the dip between the cusps. It is symmetric about x = 512.
_TOOTH: tuple[tuple[tuple[float, float], ...], ...] = (
    ((512, 330), (470, 290), (420, 270), (380, 272)),
    ((380, 272), (310, 276), (285, 340), (292, 420)),
    ((292, 420), (300, 500), (330, 540), (345, 600)),
    ((345, 600), (360, 680), (370, 760), (405, 770)),
    ((405, 770), (440, 778), (455, 700), (470, 640)),
    ((470, 640), (480, 610), (544, 610), (554, 640)),
    ((554, 640), (569, 700), (584, 778), (619, 770)),
    ((619, 770), (654, 760), (664, 680), (679, 600)),
    ((679, 600), (694, 540), (724, 500), (732, 420)),
    ((732, 420), (739, 340), (714, 276), (644, 272)),
    ((644, 272), (604, 270), (554, 290), (512, 330)),
)
_TILE = (100, 100, 924, 924)
_TILE_RADIUS = 185
_TILE_TOP = (20, 184, 166)
_TILE_BOTTOM = (15, 118, 110)


def supported() -> bool:
    return sys.platform == "darwin"


def default_applications_dir() -> Path:
    return Path.home() / "Applications"


def bundle_path(applications_dir: Path) -> Path:
    return applications_dir / f"{APP_NAME}.app"


def default_dio_path() -> Path:
    """The `dio` of this installation, as an absolute path.

    The one beside this interpreter comes first: in a `uv tool` install that is the tool's own
    environment, which `uv tool upgrade` rebuilds at the same path.
    """
    beside = Path(sys.executable).parent / "dio"
    if beside.is_file():
        return beside
    found = shutil.which("dio")
    if found is not None:
        return Path(found).absolute()
    raise LauncherError("The dio command was not found; reinstall with the install script.")


def applet_lines(dio_path: PurePath, app_dir: str | None) -> list[str]:
    """The applet's AppleScript, one line per `osacompile -e`.

    `do shell script` runs with a minimal PATH, so `dio` is named by its absolute path. The
    server it starts takes none of the applet's output pipes, so the applet returns at once
    (spec §4.3).
    """
    command = f"{shlex.quote(str(dio_path))} serve --detach"
    if app_dir:
        # A launcher made for a separate app folder (the smoke test) keeps using it.
        command = f"{APP_DIR_ENV}={shlex.quote(app_dir)} {command}"
    return [
        "try",
        f"\tdo shell script {_applescript_string(command)}",
        "on error errorMessage number errorNumber",
        # -128 is the user cancelling; anything else is the server failing to start.
        "\tif errorNumber is not -128 then display alert "
        f"{_applescript_string(f'{APP_NAME} could not start')} "
        "message errorMessage as critical",
        "end try",
    ]


def install_launcher(
    *, dio_path: PurePath, applications_dir: Path, app_dir: str | None, run: Runner | None = None
) -> Path:
    """Build the applet beside its final place, then swap it in, so a failed build leaves the
    previous launcher untouched."""
    runner = run or _run
    target = bundle_path(applications_dir)
    if target.exists():
        _ensure_ours(target)
    # A fresh macOS account may have no ~/Applications.
    applications_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".dio-launcher-", dir=applications_dir) as tmp:
        built = Path(tmp) / target.name
        args = ["osacompile", "-o", str(built)]
        for line in applet_lines(dio_path, app_dir):
            args += ["-e", line]
        runner(args)
        write_icon(built / "Contents" / "Resources" / "applet.icns")
        _patch_info(built / "Contents" / "Info.plist")
        # The icon and Info.plist changed after osacompile sealed the bundle; an ad-hoc
        # signature seals it again.
        runner(["codesign", "--force", "--sign", "-", str(built)])
        if target.exists():
            shutil.rmtree(target)
        built.rename(target)
    # Finder and the Dock cache icons by the bundle's modification date.
    os.utime(target)
    return target


def uninstall_launcher(applications_dir: Path) -> Path | None:
    """Remove the launcher; None when there was none."""
    target = bundle_path(applications_dir)
    if not target.exists():
        return None
    _ensure_ours(target)
    shutil.rmtree(target)
    return target


def write_icon(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Pillow writes every ICNS size from the 1024 px image.
    icon_image().save(path, format="ICNS")


def icon_image() -> Image.Image:
    """A white tooth on a teal rounded tile, on Apple's 1024 px icon grid."""
    scale = _SUPERSAMPLE
    side = ICON_SIZE * scale
    tile_mask = Image.new("L", (side, side), 0)
    ImageDraw.Draw(tile_mask).rounded_rectangle(
        [v * scale for v in _TILE], radius=_TILE_RADIUS * scale, fill=255
    )
    gradient = Image.linear_gradient("L").resize((side, side))
    tile = Image.composite(
        Image.new("RGB", (side, side), _TILE_BOTTOM),
        Image.new("RGB", (side, side), _TILE_TOP),
        gradient,
    )
    icon = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    icon.paste(tile, mask=tile_mask)
    outline = [(x * scale, y * scale) for x, y in _bezier_points(_TOOTH)]
    ImageDraw.Draw(icon).polygon(outline, fill=(255, 255, 255, 255))
    return icon.resize((ICON_SIZE, ICON_SIZE), Image.Resampling.LANCZOS)


def _bezier_points(
    segments: Sequence[tuple[tuple[float, float], ...]],
) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for (x0, y0), (x1, y1), (x2, y2), (x3, y3) in segments:
        # Each segment's end is the next one's start, so it is left out here.
        for step in range(_BEZIER_STEPS):
            t = step / _BEZIER_STEPS
            u = 1 - t
            points.append(
                (
                    u**3 * x0 + 3 * u**2 * t * x1 + 3 * u * t**2 * x2 + t**3 * x3,
                    u**3 * y0 + 3 * u**2 * t * y1 + 3 * u * t**2 * y2 + t**3 * y3,
                )
            )
    return points


def _applescript_string(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _patch_info(path: Path) -> None:
    with path.open("rb") as handle:
        info = plistlib.load(handle)
    info.update(
        {
            "CFBundleIdentifier": BUNDLE_ID,
            "CFBundleName": APP_NAME,
            "CFBundleDisplayName": APP_NAME,
            "CFBundleIconFile": "applet",
            # macOS wants digits and dots here; a ".dev0" suffix would be ignored or rejected.
            "CFBundleShortVersionString": Version(__version__).base_version,
        }
    )
    # Recent osacompile versions also name an icon in Assets.car, which macOS would prefer over
    # applet.icns.
    info.pop("CFBundleIconName", None)
    with path.open("wb") as handle:
        plistlib.dump(info, handle)


def _ensure_ours(target: Path) -> None:
    """Refuse to replace or delete an app that `install_launcher` did not make."""
    try:
        with (target / "Contents" / "Info.plist").open("rb") as handle:
            identifier = plistlib.load(handle).get("CFBundleIdentifier")
    except (OSError, plistlib.InvalidFileException, ValueError):
        identifier = None
    if identifier != BUNDLE_ID:
        raise LauncherError(
            f"{target} was not made by dio install-launcher; move it elsewhere, then try again."
        )


def _run(args: Sequence[str]) -> None:
    try:
        completed = subprocess.run(
            list(args), stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False
        )
    except OSError as exc:
        raise LauncherError(
            f"{args[0]} could not run: {exc.strerror or type(exc).__name__}"
        ) from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or f"exit code {completed.returncode}"
        raise LauncherError(f"{args[0]} failed: {detail}")
