"""Command-line interface (spec §4.10)."""

from collections import Counter
from pathlib import Path
from typing import Annotated

import typer

from dental_image_organizer import __version__
from dental_image_organizer.errors import DioError
from dental_image_organizer.log import configure_logging
from dental_image_organizer.services.headless import ProcedureReport, run_procedure
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
    except DioError as exc:
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
    skipped = Counter(o.skip_reason for o in report.photos if o.status == "skipped")
    if skipped:
        parts = [
            f"{count} {_SKIP_LABELS[reason]}{'s' if count > 1 else ''}"
            for reason, count in sorted(skipped.items(), key=lambda item: str(item[0]))
            if reason is not None
        ]
        total = sum(skipped.values())
        typer.echo(f"Skipped {total} file{'s' if total > 1 else ''}: {', '.join(parts)}")
