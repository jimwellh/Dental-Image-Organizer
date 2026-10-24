"""structlog configuration. CLI logs go to stderr so stdout carries only results; the server
also writes JSON lines to a rotating log file (spec §4.1)."""

import logging
import sys
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import TracebackType

import structlog
from structlog.typing import ExcInfo, Processor

# structlog names each stdlib logger after its module, so this is the parent of the app's own.
APP_LOGGER = "dental_image_organizer"
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUPS = 4

_CAUSE = "The above exception was the direct cause of the following exception:"
_CONTEXT = "During handling of the above exception, another exception occurred:"


def _stderr_logger(*_args: object) -> structlog.PrintLogger:
    # Look up sys.stderr per logger rather than once: test runners and servers replace the
    # stream after setup, and writing to a stale, closed stream would raise inside callers.
    return structlog.PrintLogger(file=sys.stderr)


def configure_logging(level: str = "INFO", *, log_file: Path | None = None) -> None:
    numeric = logging.getLevelNamesMapping()[level.upper()]
    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
    ]
    if log_file is not None:
        _configure_stdlib(numeric, shared, log_file)
        return
    structlog.configure(
        processors=[*shared, _console_renderer()],
        wrapper_class=structlog.make_filtering_bound_logger(numeric),
        logger_factory=_stderr_logger,
        cache_logger_on_first_use=False,
    )


def _console_renderer() -> structlog.dev.ConsoleRenderer:
    # The default rich traceback prints each frame's local variables, which include patient
    # names; those must never be logged (spec §4.13).
    return structlog.dev.ConsoleRenderer(
        colors=False, exception_formatter=structlog.dev.plain_traceback
    )


def _configure_stdlib(numeric: int, shared: list[Processor], log_file: Path) -> None:
    # stdlib-backed, so uvicorn's records reach the same handlers as the app's own events.
    log_file.parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        log_file, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUPS, encoding="utf-8"
    )
    file_handler.setFormatter(
        _formatter(shared, structlog.processors.JSONRenderer(ensure_ascii=False))
    )
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setFormatter(_formatter(shared, _console_renderer()))
    root = logging.getLogger()
    # Exactly these two: any other handler would render tracebacks with their messages.
    root.handlers[:] = [file_handler, console_handler]
    # Other libraries' DEBUG output, such as Pillow's dump of every EXIF tag, could carry a
    # patient's details; only the app's own DEBUG events (tuning metrics) are wanted.
    root.setLevel(max(numeric, logging.INFO))
    logging.getLogger(APP_LOGGER).setLevel(numeric)
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        wrapper_class=structlog.make_filtering_bound_logger(numeric),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )


def _formatter(shared: list[Processor], renderer: Processor) -> logging.Formatter:
    return structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.ExceptionRenderer(format_exception_safely),
            renderer,
        ],
        # Records from the stdlib loggers, such as uvicorn's, get the same fields.
        foreign_pre_chain=shared,
    )


def format_exception_safely(exc_info: ExcInfo) -> str:
    """A traceback without messages, source lines, or local variables.

    Exception messages and an OSError's file name carry patient names, which are never logged
    (spec §4.13); the types along the chain and the frames still show where it failed.
    """
    return "\n".join(_format_chain(exc_info[1], exc_info[2], set()))


def _format_chain(
    exc: BaseException, tb: TracebackType | None, seen: set[int], indent: str = ""
) -> list[str]:
    # Oldest first, as Python prints a chain.
    chain: list[tuple[BaseException, TracebackType | None, str | None]] = []
    link: str | None = None
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append((current, tb, link))
        if current.__cause__ is not None:
            current, link = current.__cause__, _CAUSE
        elif current.__context__ is not None and not current.__suppress_context__:
            current, link = current.__context__, _CONTEXT
        else:
            current = None
        tb = current.__traceback__ if current is not None else None
    lines: list[str] = []
    for item, item_tb, item_link in reversed(chain):
        lines += _format_one(item, item_tb, seen, indent)
        if item_link is not None:
            lines += ["", f"{indent}{item_link}", ""]
    return lines


def _format_one(
    exc: BaseException, tb: TracebackType | None, seen: set[int], indent: str
) -> list[str]:
    lines = [f"{indent}Traceback (most recent call last):"] if tb is not None else []
    lines += [
        f"{indent}  {frame.f_code.co_filename}:{lineno} in {frame.f_code.co_name}"
        for frame, lineno in traceback.walk_tb(tb)
    ]
    lines.append(f"{indent}{_describe(exc)}")
    if isinstance(exc, BaseExceptionGroup):
        # The group says little; its members say what failed.
        for number, member in enumerate(exc.exceptions, start=1):
            lines.append(f"{indent}+-- {number} of {len(exc.exceptions)}")
            lines += _format_chain(member, member.__traceback__, seen, indent + "    ")
    return lines


def _describe(exc: BaseException) -> str:
    cls = type(exc)
    name = cls.__qualname__
    if cls.__module__ not in ("builtins", "__main__"):
        name = f"{cls.__module__}.{name}"
    if isinstance(exc, OSError) and exc.strerror:
        # strerror is the OS's text alone; str(exc) would add the file name.
        errno = f"[Errno {exc.errno}] " if exc.errno is not None else ""
        return f"{name}: {errno}{exc.strerror}"
    return name
