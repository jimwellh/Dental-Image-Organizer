"""Single-instance guard and the record of the running server (spec §4.3)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from filelock import FileLock, Timeout

from dental_image_organizer.storage.atomic import write_atomic


@dataclass(frozen=True)
class InstanceInfo:
    port: int
    pid: int
    version: str
    token: str | None


class InstanceLock:
    """An OS file lock: it dies with its process, so a crash never leaves a stale lock."""

    def __init__(self, path: Path) -> None:
        self._path = path
        # thread_local=False: the server acquires on one thread and may release on another.
        self._lock = FileLock(path, timeout=0, thread_local=False)

    def try_acquire(self) -> bool:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._lock.acquire()
        except Timeout:
            return False
        return True

    def release(self) -> None:
        self._lock.release()

    @property
    def held(self) -> bool:
        return self._lock.is_locked


def read_instance(path: Path) -> InstanceInfo | None:
    """The recorded server, or None when the file is missing or not ours (a stale hint at most)."""
    try:
        raw = json.loads(path.read_bytes())
        port, pid, version, token = raw["port"], raw["pid"], raw["version"], raw.get("token")
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
    if not (_is_int(port) and _is_int(pid) and isinstance(version, str)):
        return None
    if token is not None and not isinstance(token, str):
        return None
    return InstanceInfo(port=port, pid=pid, version=version, token=token)


def write_instance(path: Path, info: InstanceInfo) -> None:
    write_atomic(path, json.dumps(asdict(info)).encode())


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
