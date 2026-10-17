import io
import sys

import pytest
import structlog

from dental_image_organizer.log import configure_logging


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
