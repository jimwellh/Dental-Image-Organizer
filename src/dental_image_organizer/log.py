"""structlog configuration. CLI logs go to stderr so stdout carries only results."""

import logging
import sys

import structlog


def _stderr_logger(*_args: object) -> structlog.PrintLogger:
    # Look up sys.stderr per logger rather than once: test runners and servers replace the
    # stream after setup, and writing to a stale, closed stream would raise inside callers.
    return structlog.PrintLogger(file=sys.stderr)


def configure_logging(level: str = "INFO") -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(colors=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[level.upper()]
        ),
        logger_factory=_stderr_logger,
        cache_logger_on_first_use=False,
    )
