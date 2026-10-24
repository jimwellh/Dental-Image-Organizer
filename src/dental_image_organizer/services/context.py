"""The composition root: one context per server, which every service function takes (spec §4.3)."""

from __future__ import annotations

import os
import shutil
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import httpx
import structlog

from dental_image_organizer.config import Settings, SettingsStore
from dental_image_organizer.errors import JobRunningError, StorageError
from dental_image_organizer.ml.model_store import DEFAULT_MODEL, ModelSpec
from dental_image_organizer.paths import AppDirs
from dental_image_organizer.services.ai import AiRuntime
from dental_image_organizer.services.jobs import JobManager, JobSnapshot
from dental_image_organizer.services.originals import ReferenceCache
from dental_image_organizer.services.teach import TeachSessions
from dental_image_organizer.storage.cache import BlobCache
from dental_image_organizer.storage.library import CaseLocks, Library

log = structlog.get_logger(__name__)

WORKERS = min(4, os.cpu_count() or 1)
# Decimal, as Finder counts the cache folder's size.
BYTES_PER_GB = 1_000_000_000


@dataclass
class AppContext:
    dirs: AppDirs
    settings: SettingsStore
    case_locks: CaseLocks
    cache: BlobCache
    jobs: JobManager
    ai: AiRuntime
    references: ReferenceCache
    executor: ThreadPoolExecutor
    # One Library per root: it holds the scan index, so every caller must share it.
    _library: Library = field(init=False, repr=False)
    teach: TeachSessions = field(init=False)
    _settings_lock: threading.Lock = field(init=False, repr=False, default_factory=threading.Lock)

    def __post_init__(self) -> None:
        self._library = Library(self.settings.get().library_root, self.case_locks)

    def library(self) -> Library:
        return self._library

    def apply_settings(self, new: Settings) -> Settings:
        # model_copy skips validation; a value the store would refuse to load back must never
        # be saved, because it would keep the app from starting.
        checked = Settings.model_validate(new.model_dump())
        with self._settings_lock:
            moved = checked.library_root != self.settings.get().library_root
            if moved:
                if self.jobs.active():
                    raise JobRunningError(
                        "Wait for the running jobs to finish before moving the library."
                    )
                _make_root(checked.library_root)
            saved = self.settings.put(checked)
            if moved:
                self._library = Library(saved.library_root, self.case_locks)
                log.info("library_moved")
            self.cache.set_limit(_cache_bytes(saved))
            _evict(self.cache)
            return saved

    def close(self) -> None:
        self.jobs.cancel_queued()
        self.executor.shutdown(wait=True)


def create_context(
    dirs: AppDirs,
    *,
    model_spec: ModelSpec = DEFAULT_MODEL,
    workers: int = WORKERS,
    http_client: Callable[[], httpx.Client] | None = None,
) -> AppContext:
    settings = SettingsStore(dirs.settings_file)
    current = settings.get()
    _make_root(current.library_root)
    # Zips and teaching sessions never outlive a run (spec §3.5, §4.9).
    _empty(dirs.exports)
    _empty(dirs.teach)
    cache = BlobCache(dirs.renders, _cache_bytes(current))
    executor = ThreadPoolExecutor(
        max_workers=workers, thread_name_prefix="dio-worker", initializer=_init_worker
    )

    def evict_after(_: JobSnapshot) -> None:
        _evict(cache)

    ctx = AppContext(
        dirs=dirs,
        settings=settings,
        case_locks=CaseLocks(),
        cache=cache,
        jobs=JobManager(executor, on_finish=evict_after),
        ai=AiRuntime(dirs, model_spec, http_client=http_client),
        references=ReferenceCache(),
        executor=executor,
    )
    ctx.teach = TeachSessions(ctx)
    # Only at startup: later in the run, an import in this process may be staging its folder.
    ctx.library().clean_staging()
    _evict(cache)
    return ctx


def _evict(cache: BlobCache) -> None:
    try:
        cache.evict()
    except OSError as exc:
        # Housekeeping only: what cannot be removed now goes at the next eviction, and neither
        # startup nor a settings change that was already saved may fail because of it.
        log.warning("cache_evict_failed", error=exc.strerror or type(exc).__name__)


def _init_worker() -> None:
    # OpenCV's own thread pool inside every worker would oversubscribe the cores (spec §4.3).
    cv2.setNumThreads(1)


def _cache_bytes(settings: Settings) -> int:
    return int(settings.cache_max_gb * BYTES_PER_GB)


def _make_root(root: Path) -> None:
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise StorageError(
            f"The library folder could not be created: {exc.strerror or type(exc).__name__}"
        ) from exc


def _empty(folder: Path) -> None:
    """Remove everything inside `folder`, which is kept; leftovers only cost disk space."""
    try:
        with os.scandir(folder) as it:
            entries = list(it)
    except FileNotFoundError:
        entries = []
    except OSError as exc:
        log.warning("temp_folder_unreadable", error=exc.strerror or type(exc).__name__)
        return
    for entry in entries:
        try:
            if entry.is_dir(follow_symlinks=False):
                shutil.rmtree(entry.path)
            else:
                os.unlink(entry.path)
        except OSError as exc:
            # Housekeeping only: a file another program holds open must not block startup.
            log.warning("temp_remove_failed", error=exc.strerror or type(exc).__name__)
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.warning("temp_folder_create_failed", error=exc.strerror or type(exc).__name__)
