import io
import json
import logging
import re
import sys
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import pytest
import structlog

from dental_image_organizer.log import WITHHELD_MESSAGE, configure_logging

# Built at runtime so the name is only a value, never part of a source line in a trace.
PATIENT = " ".join(["Alex", "Wang"])


def file_logging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, level: str = "INFO") -> Path:
    """Configure file logging; the log file's path. stderr is `sys.stderr`, a StringIO."""
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    log_file = tmp_path / "log" / "dio.log"
    configure_logging(level, log_file=log_file)
    return log_file


def records(log_file: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines()]


def stderr_text() -> str:
    stream = sys.stderr
    assert isinstance(stream, io.StringIO)
    return stream.getvalue()


def raise_chained_os_error() -> None:
    # The implicit __context__ chain is the case under test, so no `from`.
    try:
        raise FileNotFoundError(2, "No such file", f"/lib/{PATIENT}/x.jpg")
    except FileNotFoundError:
        raise RuntimeError(PATIENT)  # noqa: B904


def test_logging_follows_a_replaced_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    first, second = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stderr", first)
    configure_logging("INFO")
    first.close()  # e.g. a CLI test runner restored and closed its captured stream
    monkeypatch.setattr(sys, "stderr", second)

    structlog.get_logger("test").warning("still_logged")

    assert "still_logged" in second.getvalue()


def test_exception_logs_omit_local_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    # Locals in a traceback include patient names, which must never be logged (spec §4.13).
    stream = io.StringIO()
    monkeypatch.setattr(sys, "stderr", stream)
    configure_logging("INFO")

    def fail(patient: str) -> None:
        raise ValueError(f"boom for {len(patient)} chars")

    # Built at runtime so the name is only a local, not part of a source line in the trace.
    patient = " ".join(["Alex", "Wang"])
    try:
        fail(patient)
    except ValueError:
        structlog.get_logger("test").exception("failed")

    assert "ValueError: boom for 9 chars" in stream.getvalue()
    assert "Alex Wang" not in stream.getvalue()


def test_level_filters_lower_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = io.StringIO()
    monkeypatch.setattr(sys, "stderr", stream)
    configure_logging("WARNING")

    structlog.get_logger("test").info("hidden")
    structlog.get_logger("test").warning("shown")

    assert "hidden" not in stream.getvalue()
    assert "shown" in stream.getvalue()


def test_file_logging_writes_json_lines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log_file = file_logging(tmp_path, monkeypatch)

    structlog.get_logger("test").info("server_started", case_id="c1")

    last = records(log_file)[-1]
    assert last["event"] == "server_started"
    assert last["level"] == "info"
    assert last["case_id"] == "c1"
    assert "timestamp" in last
    assert re.search(r"\[info\s*\] server_started\s+case_id=c1", stderr_text())
    handlers = logging.getLogger().handlers
    assert [type(handler) for handler in handlers] == [RotatingFileHandler, logging.StreamHandler]


def test_file_logging_keeps_locals_out_of_tracebacks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log_file = file_logging(tmp_path, monkeypatch)

    def fail(patient: str) -> None:
        raise ValueError(f"boom for {len(patient)} chars")

    try:
        fail(PATIENT)
    except ValueError:
        structlog.get_logger("test").exception("failed")

    exception = records(log_file)[-1]["exception"]
    assert "ValueError" in exception
    assert re.search(r"test_log\.py:\d+ in fail$", exception, re.MULTILINE)
    # Spec §4.13: a message may carry what the log must never hold, so none is written.
    for output in (log_file.read_text(encoding="utf-8"), stderr_text()):
        assert PATIENT not in output
        assert "boom for 9 chars" not in output


def test_file_logging_never_writes_exception_messages_or_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log_file = file_logging(tmp_path, monkeypatch)

    try:
        raise_chained_os_error()
    except RuntimeError:
        structlog.get_logger("test").exception("failed")

    exception = records(log_file)[-1]["exception"]
    for text in ("RuntimeError", "FileNotFoundError", "No such file"):
        assert text in exception
    assert PATIENT not in log_file.read_text(encoding="utf-8")
    assert "RuntimeError" in stderr_text()
    assert PATIENT not in stderr_text()


def test_stdlib_records_reach_the_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log_file = file_logging(tmp_path, monkeypatch)

    logging.getLogger("uvicorn.error").info("Uvicorn running")

    last = records(log_file)[-1]
    assert last["event"] == "Uvicorn running"
    assert last["level"] == "info"
    assert "timestamp" in last
    assert "Uvicorn running" in stderr_text()


def test_stdlib_exception_records_are_sanitized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # uvicorn logs this after the app's own handler answered 500, and for a failure in the
    # middle of a stream, which no handler sees.
    log_file = file_logging(tmp_path, monkeypatch)

    try:
        try:
            raise_chained_os_error()
        except RuntimeError as exc:
            raise KeyError(PATIENT) from exc
    except KeyError as exc:
        logging.getLogger("uvicorn.error").error("Exception in ASGI application", exc_info=exc)

    last = records(log_file)[-1]
    assert last["event"] == "Exception in ASGI application"
    for text in ("KeyError", "RuntimeError", "FileNotFoundError", "No such file"):
        assert text in last["exception"]
    for output in (log_file.read_text(encoding="utf-8"), stderr_text()):
        assert PATIENT not in output
    assert "KeyError" in stderr_text()


def test_debug_logs_only_the_apps_own_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Other libraries' DEBUG output, such as Pillow's dump of every EXIF tag, could carry what
    # the log must never hold; the app's own DEBUG events are its tuning metrics (spec §4.13).
    log_file = file_logging(tmp_path, monkeypatch, "DEBUG")

    structlog.get_logger("dental_image_organizer.core.align").debug("aligned", inliers=42)
    logging.getLogger("PIL.TiffImagePlugin").debug("tag: Artist - value: someone")

    events = [record["event"] for record in records(log_file)]
    assert "aligned" in events
    assert not any("Artist" in event for event in events)


def test_tracebacks_in_stdlib_messages_are_withheld(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Starlette reports a failed lifespan as traceback text, which uvicorn logs as the message.
    log_file = file_logging(tmp_path, monkeypatch)
    try:
        raise_chained_os_error()
    except RuntimeError:
        text = traceback.format_exc()
    assert PATIENT in text

    logging.getLogger("uvicorn.error").error(text)
    logging.getLogger("uvicorn.error").info("with a stack", stack_info=True)

    withheld, stacked = records(log_file)[-2:]
    assert withheld["event"] == WITHHELD_MESSAGE
    assert withheld["level"] == "error"
    assert withheld["logger"] == "uvicorn.error"
    assert stacked["event"] == "with a stack"
    assert "stack_info" not in stacked
    for output in (log_file.read_text(encoding="utf-8"), stderr_text()):
        assert PATIENT not in output
        assert "Stack (most recent call last)" not in output
