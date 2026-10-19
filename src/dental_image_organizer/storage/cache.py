"""Content-addressed, size-capped cache of rendered bytes."""

import contextlib
import hashlib
import io
import os
import threading
from pathlib import Path
from uuid import uuid4

import numpy as np
import structlog

log = structlog.get_logger()


def cache_key(*parts: str) -> str:
    """Hash the parts into a key; the NUL separator keeps ("ab", "c") apart from ("a", "bc")."""
    return hashlib.sha256("\x00".join(parts).encode()).hexdigest()


def encode_array(array: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    np.save(buffer, array, allow_pickle=False)
    return buffer.getvalue()


def decode_array(data: bytes) -> np.ndarray:
    array: np.ndarray = np.load(io.BytesIO(data), allow_pickle=False)
    return array


class BlobCache:
    """Least-recently-used byte store; an entry's mtime is its LRU clock.

    atime is not used: macOS and Linux mount with relatime or noatime, so it does not move.
    """

    def __init__(self, directory: Path, max_bytes: int) -> None:
        self._dir = directory
        self._max_bytes = max_bytes
        self._limit_lock = threading.Lock()

    def set_limit(self, max_bytes: int) -> None:
        with self._limit_lock:
            self._max_bytes = max_bytes

    def _path(self, key: str) -> Path:
        return self._dir / key[:2] / key

    def get(self, key: str) -> bytes | None:
        path = self._path(key)
        try:
            data = path.read_bytes()
        except (FileNotFoundError, PermissionError):
            # Gone, or being replaced by another thread (Windows): either way, a miss.
            return None
        with contextlib.suppress(OSError):
            os.utime(path)
        return data

    def put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{key}.{uuid4().hex[:8]}.tmp")
        try:
            tmp.write_bytes(data)
            try:
                os.replace(tmp, path)
            except PermissionError:
                # Windows refuses to replace a file another thread has open. The key is
                # content-addressed, so an existing target already holds these bytes.
                if not path.exists():
                    raise
        finally:
            with contextlib.suppress(OSError):
                tmp.unlink(missing_ok=True)

    def _entries(self) -> list[tuple[float, int, Path]]:
        entries = []
        for path in self._dir.glob("??/*"):
            if path.name.startswith("."):
                continue  # a put in flight
            try:
                stat = path.stat()
            except OSError:
                continue
            entries.append((stat.st_mtime, stat.st_size, path))
        return sorted(entries)

    def evict(self) -> int:
        """Remove the oldest entries until the total fits the cap; return the bytes removed."""
        with self._limit_lock:
            limit = self._max_bytes
        entries = self._entries()
        total = sum(size for _, size, _ in entries)
        removed = 0
        for _, size, path in entries:
            if total <= limit:
                break
            try:
                path.unlink(missing_ok=True)
            except PermissionError:
                # A reader still has it open (Windows); the next eviction gets it.
                continue
            total -= size
            removed += size
        log.debug("cache_evicted", bytes=removed)
        return removed

    def clear(self) -> None:
        for _, _, path in self._entries():
            with contextlib.suppress(OSError):
                path.unlink(missing_ok=True)
