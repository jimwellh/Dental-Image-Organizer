from collections.abc import Iterator
from pathlib import Path

import pytest
import structlog

from dental_image_organizer.log import configure_logging
from dental_image_organizer.services.context import AppContext
from support.app import make_context


@pytest.fixture
def debug_logging() -> Iterator[None]:
    # Another test may have left a WARNING-level filter in the global config, which would
    # drop debug events before any processor (including the capture) sees them.
    saved = structlog.get_config()
    configure_logging("DEBUG")
    yield
    structlog.configure(**saved)


@pytest.fixture
def ctx(tmp_path: Path) -> Iterator[AppContext]:
    context = make_context(tmp_path)
    yield context
    context.close()
