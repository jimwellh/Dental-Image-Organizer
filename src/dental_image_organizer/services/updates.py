"""The daily version check against PyPI and the in-app update (spec §4.13, §7.3)."""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import httpx
import structlog
from packaging.version import InvalidVersion, Version

from dental_image_organizer import __version__
from dental_image_organizer.config import read_launcher
from dental_image_organizer.errors import JobRunningError, UpdateUnavailableError
from dental_image_organizer.paths import AppDirs
from dental_image_organizer.services.jobs import JobManager
from dental_image_organizer.storage.atomic import write_atomic

log = structlog.get_logger(__name__)

PACKAGE = "dental-image-organizer"
PYPI_URL = f"https://pypi.org/pypi/{PACKAGE}/json"
CHECK_INTERVAL = timedelta(days=1)
TIMEOUT_S = 10.0
UPGRADE_COMMAND = f"uv tool upgrade {PACKAGE}"
# How long the update script waits for the server to exit before it gives up (spec §7.3).
SERVER_EXIT_WAIT_S = 30

OutcomeStatus = Literal["ok", "failed", "timeout"]
_OUTCOMES: tuple[OutcomeStatus, ...] = ("ok", "failed", "timeout")
_FAILURES: dict[OutcomeStatus, str] = {
    "failed": "The last update failed, so the version you had was started again.",
    "timeout": "The last update did not run, because the app did not stop in time.",
}


def _now() -> datetime:
    return datetime.now(UTC)


def latest_release(payload: Any) -> str | None:
    """The highest final release in a PyPI JSON answer that still has a file nobody yanked.

    Read from `releases` rather than `info.version`, so a pre-release never counts as an update.
    """
    releases = payload.get("releases") if isinstance(payload, dict) else None
    if not isinstance(releases, dict):
        raise ValueError("The PyPI answer has no releases.")
    best: Version | None = None
    for text, files in releases.items():
        try:
            version = Version(text)
        except InvalidVersion:
            continue
        if version.is_prerelease or version.is_devrelease:
            continue
        usable = isinstance(files, list) and any(
            isinstance(f, dict) and not f.get("yanked", False) for f in files
        )
        if usable and (best is None or version > best):
            best = version
    return None if best is None else str(best)


def is_newer(latest: str, current: str) -> bool:
    try:
        return Version(latest) > Version(current)
    except InvalidVersion:
        return False


@dataclass(frozen=True)
class CheckRecord:
    checked_at: datetime
    latest: str | None


@dataclass(frozen=True)
class UpdateOutcome:
    """What the last update script reported; the next server start reads it once."""

    status: OutcomeStatus
    finished_at: datetime | None


@dataclass(frozen=True)
class UpdateInfo:
    current: str
    latest: str | None
    available: bool
    checked_at: datetime | None
    # Why "Update now" is not offered; None when it is.
    blocker: str | None
    command: str
    failure: str | None
    log_file: Path


class UpdateService:
    """One per server: the last version check, and the guard that starts one update at most."""

    def __init__(
        self,
        dirs: AppDirs,
        *,
        http_client: Callable[[], httpx.Client] | None = None,
        clock: Callable[[], datetime] = _now,
        platform: str = sys.platform,
        spawn: Callable[[Path, Path], None] | None = None,
    ) -> None:
        self._dirs = dirs
        self._http_client = http_client
        self._clock = clock
        self._platform = platform
        self._spawn = spawn or _spawn
        self._lock = threading.Lock()
        self._record = _read_record(dirs.update_check_file)
        self._started = False
        # Read once and removed, so the failure is reported by this run only.
        self.last_outcome = take_outcome(dirs.update_result_file)
        if self.last_outcome is not None:
            log.info("update_finished", status=self.last_outcome.status)

    def last_check(self) -> CheckRecord | None:
        with self._lock:
            return self._record

    def check_due(self) -> bool:
        record = self.last_check()
        if record is None:
            return True
        elapsed = self._clock() - record.checked_at
        # A clock set back would otherwise postpone the check until it catches up.
        return elapsed >= CHECK_INTERVAL or elapsed < timedelta(0)

    def check(self) -> CheckRecord:
        """Ask PyPI for the newest release. A failure keeps the last answer and still counts as
        the day's check, so an offline Mac is not asked again every hour."""
        previous = self.last_check()
        try:
            latest = self._fetch()
        except (httpx.HTTPError, ValueError, OSError, ImportError) as exc:
            # The message can carry proxy credentials from the environment, so only the type.
            log.warning("update_check_failed", error=type(exc).__name__)
            latest = previous.latest if previous else None
        record = CheckRecord(self._clock(), latest)
        try:
            payload = {"checked_at": record.checked_at.isoformat(), "latest": record.latest}
            write_atomic(self._dirs.update_check_file, json.dumps(payload).encode())
        except OSError as exc:
            log.warning("update_check_unsaved", error=exc.strerror or type(exc).__name__)
        with self._lock:
            self._record = record
        log.info("update_checked", latest=latest, current=__version__)
        return record

    def _fetch(self) -> str | None:
        factory = self._http_client or _new_client
        with factory() as client:
            response = client.get(PYPI_URL, headers={"Accept": "application/json"})
            if response.status_code == 404:
                # Not published yet: nothing is newer.
                return None
            response.raise_for_status()
            return latest_release(response.json())

    def blocker(self) -> str | None:
        """Why the in-app update cannot run on this machine, or None when it can."""
        if self._platform != "darwin":
            return "In-app update works on macOS only."
        paths = read_launcher(self._dirs.launcher_file)
        if paths is None:
            return "The app was installed without its launcher, so it cannot update itself."
        if paths.uv_path is None or not paths.uv_path.is_file():
            return "uv was not found where the launcher recorded it."
        if not paths.dio_path.is_file():
            return "The app was not found where the launcher recorded it."
        return None

    def info(self, *, check_enabled: bool) -> UpdateInfo:
        record = self.last_check() if check_enabled else None
        latest = record.latest if record is not None else None
        outcome = self.last_outcome
        return UpdateInfo(
            current=__version__,
            latest=latest,
            available=latest is not None and is_newer(latest, __version__),
            checked_at=record.checked_at if record is not None else None,
            blocker=self.blocker(),
            command=UPGRADE_COMMAND,
            failure=_FAILURES.get(outcome.status) if outcome is not None else None,
            log_file=self._dirs.update_log_file,
        )

    def start(self, jobs: JobManager, *, server_pid: int) -> None:
        """Write the update script and start it detached; the caller then stops the server.

        A script, because `uv tool upgrade` replaces the virtual environment this server runs
        from, so the upgrade must not run inside it (spec §7.3).
        """
        if jobs.active():
            raise JobRunningError("Wait for the running jobs to finish before updating.")
        reason = self.blocker()
        if reason is not None:
            raise UpdateUnavailableError(f"{reason} To update, run in Terminal: {UPGRADE_COMMAND}")
        paths = read_launcher(self._dirs.launcher_file)
        # blocker() returned None, so both paths were recorded and exist.
        assert paths is not None and paths.uv_path is not None
        with self._lock:
            if self._started:
                raise UpdateUnavailableError("An update is already starting.")
            self._started = True
        try:
            script = update_script(
                server_pid=server_pid,
                uv=paths.uv_path,
                dio=paths.dio_path,
                result_file=self._dirs.update_result_file,
            )
            write_atomic(self._dirs.update_script, script.encode("utf-8"))
            self._dirs.update_result_file.unlink(missing_ok=True)
            self._spawn(self._dirs.update_script, self._dirs.update_log_file)
        except BaseException:
            with self._lock:
                self._started = False
            raise
        log.info("update_started", current=__version__)


def update_script(*, server_pid: int, uv: Path, dio: Path, result_file: Path) -> str:
    """A POSIX shell script that waits for the server to exit, upgrades, and restarts it.

    The result is written before the restart, which reads it once (`take_outcome`).
    """
    q = shlex.quote
    return f"""#!/bin/sh
# Written by Dental Image Organizer for one in-app update (spec §7.3).
server_pid={server_pid}
uv={q(str(uv))}
dio={q(str(dio))}
result={q(str(result_file))}
wait_s={SERVER_EXIT_WAIT_S}

say() {{
  printf '%s update: %s\\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}}

finish() {{
  printf '{{"status": "%s", "finished_at": "%s"}}\\n' "$1" "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" \\
    > "$result.tmp" && mv -f "$result.tmp" "$result"
}}

say "waiting for the server (pid $server_pid) to stop"
waited=0
while kill -0 "$server_pid" 2>/dev/null; do
  if [ "$waited" -ge "$wait_s" ]; then
    # Upgrading under a running server would replace the files it is using.
    say "the server did not stop within $wait_s s; nothing was changed"
    finish timeout
    exit 1
  fi
  sleep 1
  waited=$((waited + 1))
done

say "running uv tool upgrade {PACKAGE}"
"$uv" tool upgrade {PACKAGE}
code=$?
if [ "$code" -eq 0 ]; then
  finish ok
else
  say "uv tool upgrade failed with exit code $code; starting the current version again"
  finish failed
fi

say "starting the server"
exec "$dio" serve --detach --no-browser
"""


def take_outcome(path: Path) -> UpdateOutcome | None:
    try:
        raw = json.loads(path.read_bytes())
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        raw = None
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        log.warning("update_result_unremovable", error=exc.strerror or type(exc).__name__)
    status = raw.get("status") if isinstance(raw, dict) else None
    if status not in _OUTCOMES:
        # A script cut off while writing still means the update did not finish.
        return UpdateOutcome("failed", None)
    finished = raw.get("finished_at") if isinstance(raw, dict) else None
    try:
        finished_at = datetime.fromisoformat(finished) if isinstance(finished, str) else None
    except ValueError:
        finished_at = None
    return UpdateOutcome(status, finished_at)


def _read_record(path: Path) -> CheckRecord | None:
    try:
        raw = json.loads(path.read_bytes())
        checked_at = datetime.fromisoformat(raw["checked_at"])
        latest = raw["latest"]
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError) as exc:
        # Only a hint: the next check rewrites it.
        log.warning("update_check_record_unreadable", error=type(exc).__name__)
        return None
    if checked_at.tzinfo is None or (latest is not None and not isinstance(latest, str)):
        return None
    return CheckRecord(checked_at, latest)


def _new_client() -> httpx.Client:
    # The User-Agent names only the app and its version; nothing about the user is sent.
    return httpx.Client(
        timeout=TIMEOUT_S,
        follow_redirects=True,
        headers={"User-Agent": f"{PACKAGE}/{__version__}"},
    )


def _spawn(script: Path, log_file: Path) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("ab") as output:
        # Its own session, so it outlives the server; its output goes to update.log rather than
        # dio.log, which holds JSON lines and rotates.
        subprocess.Popen(
            ["/bin/sh", str(script)],
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            close_fds=True,
            start_new_session=True,
        )
