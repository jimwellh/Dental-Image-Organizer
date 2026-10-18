"""Command-line interface (spec §4.10)."""

from collections import Counter
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Annotated

import typer

from dental_image_organizer import __version__
from dental_image_organizer.core.slots import DEFAULT_FLIPS, FLIPS, SLOTS, Flip, Slot, parse_slot
from dental_image_organizer.errors import DioError
from dental_image_organizer.log import configure_logging
from dental_image_organizer.services.composite import DEFAULT_BASELINE
from dental_image_organizer.services.headless import PhotoOutcome, ProcedureReport, run_procedure
from dental_image_organizer.services.headless_composite import (
    CompositeOptions,
    CompositeReport,
    run_composite,
)
from dental_image_organizer.services.procedure import OUTPUT_SCALES

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
        typer.Option(help="FILE=SLOT or FILE=tray: place a photo by hand; repeatable."),
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
    try:
        report = run_composite(
            photos, out, patient=patient, tag=tag, options=options, notify=typer.echo
        )
    except (DioError, OSError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    _print_composite(report, learn=learn)
    if report.failed:
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


def _parse_assign(items: Iterable[str], photos: Sequence[Path]) -> dict[str, Slot | None]:
    names = {p.name for p in photos}
    assign: dict[str, Slot | None] = {}
    for item in items:
        # File names may contain "=", slot names never do.
        name, sep, target = item.rpartition("=")
        if not sep:
            raise typer.BadParameter(
                f"expected FILE=SLOT or FILE=tray, got {item!r}", param_hint="--assign"
            )
        if name not in names:
            raise typer.BadParameter(f"{name!r} is not one of the photos", param_hint="--assign")
        if name in assign:
            raise typer.BadParameter(f"{name!r} is assigned twice", param_hint="--assign")
        slot = None if target.strip() == "tray" else _parse_slot_option(target, "--assign")
        if slot is not None and slot in assign.values():
            raise typer.BadParameter(f"two files are assigned to {slot}", param_hint="--assign")
        assign[name] = slot
    return assign


def _print_composite(report: CompositeReport, *, learn: bool) -> None:
    for cell in report.cells:
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
    typer.echo(f"{'composite':<16} {'':>5}  -> {report.output.name}")
    for note in report.notes:
        typer.echo(note)
    if learn:
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
