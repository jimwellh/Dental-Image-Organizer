import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
import structlog

from dental_image_organizer.log import APP_LOGGER, configure_logging
from dental_image_organizer.services.context import AppContext
from support.app import make_context


@pytest.fixture(autouse=True)
def restore_logging() -> Iterator[None]:
    # `serve()` and the log tests install file logging on the root logger, which would
    # otherwise outlive the test and write later tests' events into its folder.
    root, app_logger = logging.getLogger(), logging.getLogger(APP_LOGGER)
    handlers, root_level, app_level = list(root.handlers), root.level, app_logger.level
    config = structlog.get_config()
    yield
    for handler in root.handlers:
        if handler not in handlers:
            # Windows cannot delete a tmp_path that still holds an open log file.
            handler.close()
    root.handlers[:] = handlers
    root.setLevel(root_level)
    app_logger.setLevel(app_level)
    structlog.configure(**config)


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
