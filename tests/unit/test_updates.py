"""The version check and the in-app update's guard and script (spec §4.13, §7.3)."""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from dental_image_organizer import __version__
from dental_image_organizer.config import LauncherPaths, write_launcher
from dental_image_organizer.errors import JobRunningError, UpdateUnavailableError
from dental_image_organizer.paths import AppDirs
from dental_image_organizer.services import updates
from dental_image_organizer.services.context import AppContext
from dental_image_organizer.services.jobs import JobProgress
from dental_image_organizer.services.updates import (
    PYPI_URL,
    CheckRecord,
    UpdateOutcome,
    UpdateService,
    is_newer,
    latest_release,
    take_outcome,
    update_script,
)

NOW = datetime(2026, 10, 26, 9, 0, tzinfo=UTC)
WAIT_SECONDS = 30.0
SH = shutil.which("sh")


def _file(yanked: bool = False) -> dict[str, Any]:
    return {"filename": "x.whl", "yanked": yanked}


def _pypi(*versions: str, yanked: tuple[str, ...] = ()) -> dict[str, Any]:
    return {"releases": {v: [_file(v in yanked)] for v in versions}}


class Clock:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def _dirs(root: Path) -> AppDirs:
    return AppDirs(root / "config", root / "data", root / "cache", root / "log")


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> Callable[[], httpx.Client]:
    return lambda: httpx.Client(transport=httpx.MockTransport(handler))


def _answer(payload: Any, status: int = 200) -> Callable[[], httpx.Client]:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == PYPI_URL
        return httpx.Response(status, json=payload)

    return _client(handler)


def _offline() -> Callable[[], httpx.Client]:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    return _client(handler)


def _service(dirs: AppDirs, **kwargs: Any) -> UpdateService:
    kwargs.setdefault("clock", Clock())
    return UpdateService(dirs, **kwargs)


def _launcher(dirs: AppDirs, root: Path, *, uv: bool = True) -> LauncherPaths:
    bin_dir = root / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    (bin_dir / "dio").write_text("")
    if uv:
        (bin_dir / "uv").write_text("")
    paths = LauncherPaths(uv_path=bin_dir / "uv", dio_path=bin_dir / "dio")
    write_launcher(dirs.launcher_file, paths)
    return paths


# --- PyPI answer ------------------------------------------------------------------------------


def test_latest_release_takes_the_highest_final_release() -> None:
    assert latest_release(_pypi("0.9.0", "0.10.0", "0.2.0")) == "0.10.0"


def test_latest_release_skips_pre_releases_yanked_files_and_bad_versions() -> None:
    payload = _pypi("0.1.0", "0.2.0", "0.3.0rc1", "0.4.0.dev1", "not-a-version", yanked=("0.2.0",))
    payload["releases"]["0.5.0"] = []

    assert latest_release(payload) == "0.1.0"


def test_latest_release_is_none_without_a_usable_release() -> None:
    assert latest_release(_pypi("1.0.0a1")) is None


@pytest.mark.parametrize("payload", [None, [], {"info": {}}, {"releases": []}])
def test_latest_release_rejects_an_answer_without_releases(payload: Any) -> None:
    with pytest.raises(ValueError):
        latest_release(payload)


@pytest.mark.parametrize(
    ("latest", "current", "newer"),
    [
        ("0.2.0", "0.1.0", True),
        ("0.1.0", "0.1.0", False),
        ("0.1.0", "0.1.0.dev0", True),
        ("0.1.0", "0.2.0", False),
        ("garbage", "0.1.0", False),
    ],
)
def test_is_newer(latest: str, current: str, newer: bool) -> None:
    assert is_newer(latest, current) is newer


# --- daily check ------------------------------------------------------------------------------


def test_check_records_the_answer_and_survives_a_restart(tmp_path: Path) -> None:
    dirs = _dirs(tmp_path)
    service = _service(dirs, http_client=_answer(_pypi("9.0.0")))
    assert service.check_due()

    record = service.check()

    assert record == CheckRecord(NOW, "9.0.0")
    assert not service.check_due()
    assert _service(dirs).last_check() == record


def test_a_failed_check_keeps_the_last_answer_and_counts_as_the_days_check(
    tmp_path: Path,
) -> None:
    dirs = _dirs(tmp_path)
    clock = Clock()
    _service(dirs, clock=clock, http_client=_answer(_pypi("9.0.0"))).check()
    clock.now = NOW + timedelta(days=1)
    service = _service(dirs, clock=clock, http_client=_offline())
    assert service.check_due()

    record = service.check()

    assert record == CheckRecord(clock.now, "9.0.0")
    assert not service.check_due()


@pytest.mark.parametrize(
    ("client", "latest"),
    [
        (_answer({"message": "Not Found"}, status=404), None),
        (_answer({"oops": True}), None),
        (_answer({}, status=503), None),
    ],
)
def test_an_unpublished_or_broken_answer_finds_nothing_newer(
    tmp_path: Path, client: Callable[[], httpx.Client], latest: str | None
) -> None:
    assert _service(_dirs(tmp_path), http_client=client).check().latest == latest


def test_the_check_is_due_again_after_a_day_or_when_the_clock_went_back(tmp_path: Path) -> None:
    clock = Clock()
    service = _service(_dirs(tmp_path), clock=clock, http_client=_answer(_pypi("1.0.0")))
    service.check()

    clock.now = NOW + timedelta(hours=23)
    assert not service.check_due()
    clock.now = NOW + timedelta(days=1)
    assert service.check_due()
    clock.now = NOW - timedelta(minutes=5)
    assert service.check_due()


@pytest.mark.parametrize(
    "content",
    [b"{", b"[]", b'{"checked_at": "2026-10-26T09:00:00", "latest": "1.0"}', b'{"latest": 3}'],
)
def test_an_unusable_record_means_a_check_is_due(tmp_path: Path, content: bytes) -> None:
    dirs = _dirs(tmp_path)
    dirs.update_check_file.parent.mkdir(parents=True)
    dirs.update_check_file.write_bytes(content)

    service = _service(dirs)

    assert service.last_check() is None
    assert service.check_due()


def test_info_reports_a_newer_release_only_while_the_check_is_on(tmp_path: Path) -> None:
    service = _service(_dirs(tmp_path), http_client=_answer(_pypi("999.0.0")))
    service.check()

    on = service.info(check_enabled=True)
    off = service.info(check_enabled=False)

    assert (on.current, on.latest, on.available, on.checked_at) == (
        __version__,
        "999.0.0",
        True,
        NOW,
    )
    assert (off.latest, off.available, off.checked_at) == (None, False, None)
    assert on.command == "uv tool upgrade dental-image-organizer"


# --- the last update's outcome ----------------------------------------------------------------


def test_the_outcome_is_read_once(tmp_path: Path) -> None:
    path = tmp_path / "update-result.json"
    path.write_text('{"status": "failed", "finished_at": "2026-10-26T09:00:00Z"}\n')

    assert take_outcome(path) == UpdateOutcome("failed", NOW)
    assert not path.exists()
    assert take_outcome(path) is None


@pytest.mark.parametrize("content", ["", "{", '{"status": "maybe"}', "[]"])
def test_a_damaged_outcome_counts_as_a_failed_update(tmp_path: Path, content: str) -> None:
    path = tmp_path / "update-result.json"
    path.write_text(content)

    assert take_outcome(path) == UpdateOutcome("failed", None)
    assert not path.exists()


@pytest.mark.parametrize(
    ("status", "failure"),
    [
        ("ok", None),
        ("failed", "The last update failed, so the version you had was started again."),
        ("timeout", "The last update did not run, because the app did not stop in time."),
    ],
)
def test_the_service_reports_a_failed_last_update(
    tmp_path: Path, status: str, failure: str | None
) -> None:
    dirs = _dirs(tmp_path)
    dirs.update_result_file.parent.mkdir(parents=True)
    dirs.update_result_file.write_text(json.dumps({"status": status}))

    info = _service(dirs).info(check_enabled=True)

    assert info.failure == failure
    assert info.log_file == dirs.update_log_file


# --- starting an update -----------------------------------------------------------------------


def test_only_macos_updates_in_the_app(tmp_path: Path) -> None:
    dirs = _dirs(tmp_path)
    _launcher(dirs, tmp_path)

    assert _service(dirs, platform="win32").blocker() == "In-app update works on macOS only."
    assert _service(dirs, platform="darwin").blocker() is None


def test_the_update_needs_the_launcher_record_and_its_files(tmp_path: Path) -> None:
    dirs = _dirs(tmp_path)
    service = _service(dirs, platform="darwin")
    assert service.blocker() is not None

    _launcher(dirs, tmp_path, uv=False)
    assert service.blocker() == "uv was not found where the launcher recorded it."

    (tmp_path / "bin" / "uv").write_text("")
    (tmp_path / "bin" / "dio").unlink()
    assert service.blocker() == "The app was not found where the launcher recorded it."


class Spawned:
    def __init__(self) -> None:
        self.calls: list[tuple[Path, Path]] = []

    def __call__(self, script: Path, log_file: Path) -> None:
        self.calls.append((script, log_file))


@pytest.fixture
def held_job(ctx: AppContext) -> Iterator[None]:
    release = threading.Event()

    def hold(progress: JobProgress) -> None:
        release.wait(WAIT_SECONDS)

    job = ctx.jobs.submit("hold", hold)
    yield
    release.set()
    ctx.jobs.wait(job.id, WAIT_SECONDS)


def test_start_writes_the_script_and_starts_it_once(ctx: AppContext, tmp_path: Path) -> None:
    paths = _launcher(ctx.dirs, tmp_path)
    ctx.dirs.update_result_file.parent.mkdir(parents=True, exist_ok=True)
    ctx.dirs.update_result_file.write_text('{"status": "ok"}')
    spawned = Spawned()
    service = _service(ctx.dirs, platform="darwin", spawn=spawned)
    # The stale result was consumed at construction; a new one must not be mistaken for it.
    ctx.dirs.update_result_file.write_text('{"status": "ok"}')

    service.start(ctx.jobs, server_pid=4321)

    assert spawned.calls == [(ctx.dirs.update_script, ctx.dirs.update_log_file)]
    script = ctx.dirs.update_script.read_text(encoding="utf-8")
    assert "server_pid=4321" in script
    assert str(paths.uv_path) in script and str(paths.dio_path) in script
    assert not ctx.dirs.update_result_file.exists()
    with pytest.raises(UpdateUnavailableError, match="already starting"):
        service.start(ctx.jobs, server_pid=4321)
    assert len(spawned.calls) == 1


def test_start_is_refused_while_a_job_runs(ctx: AppContext, tmp_path: Path, held_job: None) -> None:
    _launcher(ctx.dirs, tmp_path)
    spawned = Spawned()

    with pytest.raises(JobRunningError):
        _service(ctx.dirs, platform="darwin", spawn=spawned).start(ctx.jobs, server_pid=1)
    assert spawned.calls == []


def test_start_names_the_terminal_command_where_it_cannot_update(ctx: AppContext) -> None:
    spawned = Spawned()

    with pytest.raises(UpdateUnavailableError, match="uv tool upgrade dental-image-organizer"):
        _service(ctx.dirs, platform="win32", spawn=spawned).start(ctx.jobs, server_pid=1)
    assert spawned.calls == []


def test_a_failed_spawn_allows_another_try(ctx: AppContext, tmp_path: Path) -> None:
    _launcher(ctx.dirs, tmp_path)
    attempts: list[None] = []

    def spawn(script: Path, log_file: Path) -> None:
        attempts.append(None)
        if len(attempts) == 1:
            raise OSError("no /bin/sh")

    service = _service(ctx.dirs, platform="darwin", spawn=spawn)
    with pytest.raises(OSError):
        service.start(ctx.jobs, server_pid=1)
    service.start(ctx.jobs, server_pid=1)
    assert len(attempts) == 2


# --- the script itself ------------------------------------------------------------------------

needs_sh = pytest.mark.skipif(SH is None, reason="runs the update script with a POSIX sh")


def _fake(path: Path, body: str) -> Path:
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8", newline="\n")
    path.chmod(0o755)
    return path


def _run_script(
    tmp_path: Path, *, uv_exit: int, server_pid: int
) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    calls = tmp_path / "calls.txt"
    log = shlex.quote(str(calls))
    uv = _fake(tmp_path / "uv", f'echo "uv $*" >> {log}\nexit {uv_exit}')
    dio = _fake(tmp_path / "dio", f'echo "dio $*" >> {log}')
    result = tmp_path / "result.json"
    script = tmp_path / "update.sh"
    script.write_text(
        update_script(server_pid=server_pid, uv=uv, dio=dio, result_file=result),
        encoding="utf-8",
        newline="\n",
    )
    assert SH is not None
    done = subprocess.run(
        [SH, str(script)], capture_output=True, text=True, timeout=WAIT_SECONDS, check=False
    )
    return done, calls, result


def _gone_pid() -> int:
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    return process.pid


@needs_sh
def test_the_script_upgrades_then_restarts(tmp_path: Path) -> None:
    done, calls, result = _run_script(tmp_path, uv_exit=0, server_pid=_gone_pid())

    assert done.returncode == 0, done.stdout + done.stderr
    assert calls.read_text().splitlines() == [
        "uv tool upgrade dental-image-organizer",
        "dio serve --detach --no-browser",
    ]
    assert json.loads(result.read_text())["status"] == "ok"


@needs_sh
def test_a_failed_upgrade_restarts_the_current_version(tmp_path: Path) -> None:
    done, calls, result = _run_script(tmp_path, uv_exit=2, server_pid=_gone_pid())

    assert done.returncode == 0
    assert calls.read_text().splitlines()[-1] == "dio serve --detach --no-browser"
    assert json.loads(result.read_text())["status"] == "failed"
    assert "exit code 2" in done.stdout
    assert take_outcome(result) is not None


@needs_sh
@pytest.mark.skipif(sys.platform == "win32", reason="MSYS kill -0 cannot see Windows pids")
def test_the_script_never_upgrades_under_a_running_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(updates, "SERVER_EXIT_WAIT_S", 1)
    server = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        done, calls, result = _run_script(tmp_path, uv_exit=0, server_pid=server.pid)
    finally:
        server.kill()
        server.wait()

    assert done.returncode == 1
    assert not calls.exists()
    assert json.loads(result.read_text())["status"] == "timeout"


@needs_sh
def test_the_script_quotes_paths_with_spaces_and_quotes(tmp_path: Path) -> None:
    odd = tmp_path / "Dr O'Neil's Mac"
    odd.mkdir()

    done, calls, result = _run_script(odd, uv_exit=0, server_pid=_gone_pid())

    assert done.returncode == 0, done.stdout + done.stderr
    assert len(calls.read_text().splitlines()) == 2
    assert json.loads(result.read_text())["status"] == "ok"
