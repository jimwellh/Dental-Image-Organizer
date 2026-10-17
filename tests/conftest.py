from collections.abc import Iterator

import pytest
import structlog

from dental_image_organizer.log import configure_logging


@pytest.fixture
def debug_logging() -> Iterator[None]:
    # Another test may have left a WARNING-level filter in the global config, which would
    # drop debug events before any processor (including the capture) sees them.
    saved = structlog.get_config()
    configure_logging("DEBUG")
    yield
    structlog.configure(**saved)
