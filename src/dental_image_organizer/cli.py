"""Command-line interface (spec §4.10)."""

import os
import shutil
from collections import Counter
from collections.abc import Iterable, Sequence
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer

from dental_image_organizer import __version__, server
from dental_image_organizer.config import LauncherPaths, SettingsStore, write_launcher
from dental_image_organizer.core.slots import DEFAULT_FLIPS, FLIPS, SLOTS, Flip, Slot, parse_slot
from dental_image_organizer.errors import CaseExistsError, DioError
from dental_image_organizer.instance import InstanceLock
from dental_image_organizer.launcher import macos
from dental_image_organizer.log import configure_logging
from dental_image_organizer.paths import APP_DIR_ENV, app_dirs
from dental_image_organizer.services.composite import DEFAULT_BASELINE
from dental_image_organizer.services.headless import PhotoOutcome, ProcedureReport, run_procedure
from dental_image_organizer.services.headless_composite import (
    CompositeOptions,
    CompositeReport,
    resolve_assign,
    run_composite,
)
from dental_image_organizer.services.procedure import OUTPUT_SCALES
from dental_image_organizer.services.updates import UPGRADE_COMMAND
from dental_image_organizer.storage.archive import import_case_archive, write_case_archive
from dental_image_organizer.storage.library import CASE_FILE, PATIENT_FILE, CaseLocks, Library

app = typer.Typer(no_args_is_help=True, add_completion=False)

_SKIP_LABELS = {
    "video": "video",
    "raw": "RAW/DNG file",
    "sidecar": "edit sidecar",
    "unknown": "unrecognized file",
}


def _print_version(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version", callback=_print_version, is_eager=True, help="Print the version and exit."
        ),
    ] = False,
) -> None:
    """Align, color-match, and compose clinical intraoral photos."""


@app.command()
def serve(
    port: Annotated[
        int | None,
        typer.Option(
            min=server.MIN_PORT,
            max=server.MAX_PORT,
            help="Use only this port. Default: the last run's port, else the first free one "
            f"of {server.PORTS[0]}-{server.PORTS[-1]}.",
        ),
    ] = None,
    no_browser: Annotated[
        bool, typer.Option("--no-browser", help="Do not open the app in the browser.")
    ] = False,
    detach: Annotated[
        bool,
        typer.Option(
            "--detach", help="Run in the background and return once it answers (the launcher)."
        ),
    ] = False,
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Log debug details, such as alignment metrics.")
    ] = False,
) -> None:
    """Start the local server and open the app in the browser."""
    start = server.serve_detached if detach else server.serve
    code = start(port=port, open_browser=not no_browser, verbose=verbose)
    if code:
        raise typer.Exit(code)


_ApplicationsDir = Annotated[
    Path | None,
    typer.Option(
        "--applications-dir",
        help="Put the app here instead of ~/Applications (for tests).",
        hidden=True,
        file_okay=False,
    ),
]


@app.command("install-launcher")
def install_launcher(
    uv_path: Annotated[
        Path | None,
        typer.Option(
            "--uv-path",
            help="The uv that installed the app, recorded for in-app updates. "
            "Default: the uv on PATH.",
            dir_okay=False,
        ),
    ] = None,
    applications_dir: _ApplicationsDir = None,
) -> None:
    """macOS: add the Dental Image Organizer app to ~/Applications, for the Dock and Spotlight."""
    _require_macos("install-launcher")
    configure_logging("WARNING")
    uv = _resolve_uv(uv_path)
    try:
        dio = macos.default_dio_path()
        target = macos.install_launcher(
            dio_path=dio,
            applications_dir=applications_dir or macos.default_applications_dir(),
            app_dir=os.environ.get(APP_DIR_ENV),
        )
        write_launcher(app_dirs().launcher_file, LauncherPaths(uv_path=uv, dio_path=dio))
    except (DioError, OSError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Created {target}")
    if uv is None:
        typer.echo(
            "uv was not found, so the app cannot update itself. "
            f"To update, run in Terminal: {UPGRADE_COMMAND}"
        )


@app.command("uninstall-launcher")
def uninstall_launcher(applications_dir: _ApplicationsDir = None) -> None:
    """macOS: remove the Dental Image Organizer app from ~/Applications; the library is kept."""
    _require_macos("uninstall-launcher")
    configure_logging("WARNING")
    try:
        removed = macos.uninstall_launcher(applications_dir or macos.default_applications_dir())
        app_dirs().launcher_file.unlink(missing_ok=True)
    except (DioError, OSError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Removed {removed}" if removed is not None else "No launcher was installed.")


def _require_macos(command: str) -> None:
    if not macos.supported():
        typer.echo(
            f"error: dio {command} is not supported on this system; it is for macOS.", err=True
        )
        raise typer.Exit(1)


def _resolve_uv(given: Path | None) -> Path | None:
    if given is not None:
        path = given.expanduser().absolute()
        if not path.is_file():
            raise typer.BadParameter(f"{given} is not a file", param_hint="--uv-path")
        return path
    found = shutil.which("uv")
    return Path(found).absolute() if found is not None else None


@app.command()
def process(
    photos: Annotated[
        list[Path],
        typer.Argument(help="Intra-op and post-op photos.", exists=True, dir_okay=False),
    ],
    reference: Annotated[
        Path,
        typer.Option(
            "--reference",
            "-r",
            help="The dentist-edited pre-op reference photo.",
            exists=True,
            dir_okay=False,
        ),
    ],
    out: Annotated[
        Path,
        typer.Option("--out", "-o", help="Output folder; created if missing.", file_okay=False),
    ],
    patient: Annotated[str, typer.Option(help="Patient name used in file names.")],
    title: Annotated[str, typer.Option(help="Procedure or site name used in file names.")],
    output_scale: Annotated[
        float, typer.Option(help="Output size relative to the reference: 1, 1.5, or 2.")
    ] = 1.0,
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Log alignment metrics.")
    ] = False,
) -> None:
    """Align photos to a reference; write individual photos and a sequence image."""
    configure_logging("DEBUG" if verbose else "WARNING")
    if output_scale not in OUTPUT_SCALES:
        raise typer.BadParameter("must be 1, 1.5, or 2", param_hint="--output-scale")
    try:
        report = run_procedure(
            reference, photos, out, patient=patient, title=title, output_scale=output_scale
        )
    except (DioError, OSError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    _print_report(report)
    if report.failed:
        raise typer.Exit(1)


def _print_report(report: ProcedureReport) -> None:
    typer.echo(f"{'reference':<13} {'':>5}  -> {report.reference_output.name}")
    for outcome in report.photos:
        if outcome.status == "skipped":
            continue
        if outcome.status == "failed":
            typer.echo(f"{'failed':<13} {'':>5}  {outcome.source.name}: {outcome.message}")
            continue
        assert outcome.output is not None and outcome.confidence is not None
        line = (
            f"{outcome.status:<13} {outcome.confidence:5.2f}  "
            f"{outcome.source.name} -> {outcome.output.name}"
        )
        typer.echo(f"{line}  ({outcome.message})" if outcome.message else line)
    typer.echo(f"{'sequence':<13} {'':>5}  -> {report.sequence_output.name}")
    _print_skips(report.photos)


@app.command()
def composite(
    photos: Annotated[
        list[Path],
        typer.Argument(help="The composite's photos, in any order.", exists=True, dir_okay=False),
    ],
    out: Annotated[
        Path,
        typer.Option("--out", "-o", help="Output folder; created if missing.", file_okay=False),
    ],
    patient: Annotated[str, typer.Option(help="Patient name used in file names.")],
    tag: Annotated[str, typer.Option(help="Case tag used in file names.")],
    order: Annotated[
        str | None,
        typer.Option(help="Shooting order: the nine slots, comma-separated. Default: row-major."),
    ] = None,
    baseline: Annotated[
        str, typer.Option(help="Slot whose color every other cell is matched to.")
    ] = DEFAULT_BASELINE,
    flip: Annotated[
        list[str] | None,
        typer.Option(help=f"SLOT=FLIP override ({', '.join(FLIPS)}); repeatable."),
    ] = None,
    assign: Annotated[
        list[str] | None,
        typer.Option(
            help="FILE=SLOT or FILE=tray: place a photo, by file name or path; repeatable."
        ),
    ] = None,
    learn: Annotated[
        bool, typer.Option("--learn", help="Store this checked run as AI classifier samples.")
    ] = False,
    download_model: Annotated[
        bool,
        typer.Option(
            "--download-model",
            help="Allow the one-time AI model download (about 24 MB, from github.com).",
        ),
    ] = False,
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Log leveling and classifier metrics.")
    ] = False,
) -> None:
    """Place, level, and color-match nine intraoral photos into one 3 x 3 composite."""
    configure_logging("DEBUG" if verbose else "WARNING")
    options = CompositeOptions(
        order=_parse_order(order) if order is not None else SLOTS,
        flips=_parse_flips(flip or []),
        baseline=_parse_slot_option(baseline, "--baseline"),
        assign=_parse_assign(assign or [], photos),
        learn=learn,
        download_model=download_model,
    )
    # Learning writes the classifier folder, which a running server writes too (spec §4.3).
    lock = InstanceLock(app_dirs().instance_lock) if learn else None
    if lock is not None and not lock.try_acquire():
        typer.echo(
            "error: Dental Image Organizer is running; teach from the app, or quit it first.",
            err=True,
        )
        raise typer.Exit(1)
    try:
        report = run_composite(
            photos, out, patient=patient, tag=tag, options=options, notify=typer.echo
        )
    except (DioError, OSError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    finally:
        if lock is not None:
            lock.release()
    _print_composite(report, learn=learn, baseline=options.baseline)
    if report.failed or report.output is None:
        raise typer.Exit(1)


def _parse_slot_option(text: str, hint: str) -> Slot:
    try:
        return parse_slot(text.strip())
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint=hint) from exc


def _parse_order(text: str) -> tuple[Slot, ...]:
    slots = tuple(_parse_slot_option(item, "--order") for item in text.split(","))
    if sorted(slots) != sorted(SLOTS):
        raise typer.BadParameter(
            "must list each of the nine slots exactly once", param_hint="--order"
        )
    return slots


def _parse_flips(items: Iterable[str]) -> dict[Slot, Flip]:
    flips = dict(DEFAULT_FLIPS)
    for item in items:
        slot_text, sep, flip_text = item.partition("=")
        flip = next((f for f in FLIPS if f == flip_text.strip()), None)
        if not sep or flip is None:
            raise typer.BadParameter(
                f"expected SLOT=FLIP with FLIP one of {', '.join(FLIPS)}, got {item!r}",
                param_hint="--flip",
            )
        flips[_parse_slot_option(slot_text, "--flip")] = flip
    return flips


def _parse_assign(items: Iterable[str], photos: Sequence[Path]) -> dict[int, Slot | None]:
    entries: list[tuple[str, Slot | None]] = []
    for item in items:
        # File names may contain "=", slot names never do.
        name, sep, target = item.rpartition("=")
        if not sep:
            raise typer.BadParameter(
                f"expected FILE=SLOT or FILE=tray, got {item!r}", param_hint="--assign"
            )
        slot = None if target.strip() == "tray" else _parse_slot_option(target, "--assign")
        entries.append((name, slot))
    try:
        return resolve_assign(entries, photos)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--assign") from exc


def _print_composite(report: CompositeReport, *, learn: bool, baseline: Slot) -> None:
    cells = {cell.slot: cell for cell in report.cells}
    for slot in SLOTS:
        cell = cells.get(slot)
        if cell is None:
            typer.echo(f"{slot:<16} {'':>5}  (empty)")
            continue
        if cell.confidence is None:
            confidence = f"{'':>5}"
        else:
            mark = "?" if cell.slot in report.low_confidence else " "
            confidence = f"{mark}{cell.confidence:.2f}"
        level = f"level {cell.level_deg:+.1f} deg" if cell.leveled else "level -"
        typer.echo(f"{cell.slot:<16} {confidence}  {cell.source.name}  {level}  flip {cell.flip}")
    for source in report.tray:
        typer.echo(f"{'tray':<16} {'':>5}  {source.name}")
    for outcome in report.problems:
        if outcome.status == "failed":
            typer.echo(f"{'failed':<16} {'':>5}  {outcome.source.name}: {outcome.message}")
    if report.output is None:
        typer.echo(f"{'composite':<16} {'':>5}  not written: no photo could be placed")
    else:
        typer.echo(f"{'composite':<16} {'':>5}  -> {report.output.name}")
        if report.baseline is not None and report.baseline != baseline:
            typer.echo(
                f"Colors were matched to {report.baseline} because {baseline} has no usable photo."
            )
    for note in report.notes:
        typer.echo(note)
    if learn and report.output is not None:
        if report.learn_error is not None:
            typer.echo(
                f"Learning failed: {report.learn_error}. "
                "The composite was written; nothing was learned."
            )
        else:
            state = "trained" if report.trained else "not trained yet"
            typer.echo(f"Learned {report.learned} samples; the AI classifier is {state}.")
    _print_skips(report.problems)


def _print_skips(outcomes: Iterable[PhotoOutcome]) -> None:
    skipped = Counter(o.skip_reason for o in outcomes if o.status == "skipped")
    if skipped:
        parts = [
            f"{count} {_SKIP_LABELS[reason]}{'s' if count > 1 else ''}"
            for reason, count in sorted(skipped.items(), key=lambda item: str(item[0]))
            if reason is not None
        ]
        total = sum(skipped.values())
        typer.echo(f"Skipped {total} file{'s' if total > 1 else ''}: {', '.join(parts)}")


class _OnConflict(StrEnum):
    skip = "skip"
    copy = "copy"


@app.command("export-case")
def export_case(
    case_dir: Annotated[Path, typer.Argument(help="A case folder of the library.")],
    zip_path: Annotated[Path, typer.Argument(metavar="ZIP", help="The archive to write.")],
) -> None:
    """Write one case, with its patient record and originals, to a zip archive."""
    configure_logging("WARNING")
    folder = case_dir.resolve()
    if not (folder / CASE_FILE).is_file() or not (folder.parent / PATIENT_FILE).is_file():
        typer.echo(f"error: {case_dir} is not a case folder.", err=True)
        raise typer.Exit(1)
    if zip_path.exists():
        typer.echo(f"error: {zip_path} already exists.", err=True)
        raise typer.Exit(1)
    try:
        write_case_archive(folder, zip_path)
    except (DioError, OSError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"Wrote {zip_path}")


@app.command("import-case")
def import_case(
    zip_path: Annotated[
        Path, typer.Argument(metavar="ZIP", help="A case archive.", exists=True, dir_okay=False)
    ],
    on_conflict: Annotated[
        _OnConflict | None,
        typer.Option(help="What to do when the case is already in the library."),
    ] = None,
) -> None:
    """Add a case archive to the library."""
    configure_logging("WARNING")
    dirs = app_dirs()
    lock = InstanceLock(dirs.instance_lock)
    # The lock guards the library (spec §4.3): a running server would not see this import.
    if not lock.try_acquire():
        typer.echo(
            "error: Dental Image Organizer is running; import the case from the app, "
            "or quit it first.",
            err=True,
        )
        raise typer.Exit(1)
    try:
        library = Library(SettingsStore(dirs.settings_file).get().library_root, CaseLocks())
        library.clean_staging()
        outcome = import_case_archive(
            library, zip_path, on_conflict=on_conflict.value if on_conflict else "ask"
        )
    except CaseExistsError as exc:
        title = exc.details.get("title", "")
        typer.echo(
            f"error: This case is already in the library: {title}. "
            "Rerun with --on-conflict skip or --on-conflict copy.",
            err=True,
        )
        raise typer.Exit(1) from exc
    except (DioError, OSError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    finally:
        lock.release()
    if outcome.status == "skipped":
        typer.echo(f'Skipped "{outcome.title}": it is already in the library.')
    else:
        typer.echo(f'Imported "{outcome.title}"')
