"""`dio serve` as real processes over a throw-away app folder (spec §4.3)."""

from __future__ import annotations

import contextlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest

from dental_image_organizer.config import Settings, SettingsStore
from dental_image_organizer.instance import InstanceInfo, InstanceLock, read_instance
from dental_image_organizer.paths import APP_DIR_ENV, AppDirs, app_dirs

LAUNCH_TIMEOUT_S = 30.0
SECOND_LAUNCH_TIMEOUT_S = 10.0
STOP_TIMEOUT_S = 10.0
POLL_S = 0.2


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


def wait_until(condition: Callable[[], bool], timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() >= deadline:
            return False
        time.sleep(POLL_S)
    return True


def client(port: int) -> httpx.Client:
    # trust_env=False: a proxy in the environment must not carry these local requests.
    return httpx.Client(
        base_url=f"http://127.0.0.1:{port}",
        headers={"Origin": f"http://127.0.0.1:{port}"},
        trust_env=False,
        timeout=5.0,
    )


def library_free(dirs: AppDirs) -> bool:
    lock = InstanceLock(dirs.instance_lock)
    if not lock.try_acquire():
        return False
    lock.release()
    return True


def refuses(port: int) -> bool:
    try:
        socket.create_connection(("127.0.0.1", port), timeout=1.0).close()
    except OSError:
        return True
    return False


def stop(dirs: AppDirs) -> None:
    """End the server of `dirs`, if one runs: by its Quit endpoint, else by its recorded pid."""
    deadline = time.monotonic() + STOP_TIMEOUT_S
    while not library_free(dirs):
        info = read_instance(dirs.instance_file)
        if time.monotonic() >= deadline:
            if info is not None:
                with contextlib.suppress(OSError):
                    os.kill(info.pid, signal.SIGTERM)
            break
        if info is not None:
            with contextlib.suppress(httpx.HTTPError), client(info.port) as http:
                http.post("/api/system/shutdown")
        time.sleep(POLL_S)
    assert wait_until(lambda: library_free(dirs), STOP_TIMEOUT_S), "a test server kept running"


@pytest.fixture
def dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[AppDirs]:
    monkeypatch.setenv(APP_DIR_ENV, str(tmp_path / "app"))
    app = app_dirs()
    # A test server must not ask PyPI for the newest version.
    SettingsStore(app.settings_file).put(Settings(update_check=False))
    try:
        yield app
    finally:
        # No server may outlive its test, or the session.
        stop(app)


def launch(*args: str) -> subprocess.Popen[str]:
    # os.environ carries the monkeypatched DIO_APP_DIR, so the server never sees the real folders.
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "dental_image_organizer",
            "serve",
            "--detach",
            "--no-browser",
            *args,
        ],
        env=dict(os.environ),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
    )


def finish(process: subprocess.Popen[str], timeout: float) -> tuple[int, str, str]:
    try:
        out, err = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise
    return process.returncode, out, err


def start(*args: str, timeout: float = LAUNCH_TIMEOUT_S) -> tuple[str, str]:
    code, out, err = finish(launch(*args), timeout)
    assert code == 0, err
    return out, err


def recorded(dirs: AppDirs) -> InstanceInfo:
    info = read_instance(dirs.instance_file)
    assert info is not None
    return info


def logged_events(dirs: AppDirs) -> list[str]:
    lines = dirs.log_file.read_text(encoding="utf-8").splitlines()
    return [json.loads(line)["event"] for line in lines]


def running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_detached_server_starts_answers_and_quits(dirs: AppDirs) -> None:
    port = free_port()

    start("--port", str(port))

    with client(port) as http:
        health = http.get("/api/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"
        assert http.post("/api/system/shutdown").status_code == 202
    assert wait_until(lambda: refuses(port), STOP_TIMEOUT_S)
    assert wait_until(lambda: library_free(dirs), STOP_TIMEOUT_S)
    events = logged_events(dirs)
    # The app's own events and uvicorn's stdlib records, both as JSON lines.
    assert "server_started" in events
    assert "Application startup complete." in events
    assert "server_stopped" in events


@pytest.mark.skipif(
    sys.platform == "win32", reason="os.kill on Windows ends a process at once, like SIGKILL"
)
def test_sigterm_drains_the_server_and_releases_the_library(dirs: AppDirs) -> None:
    # macOS sends SIGTERM at logout and from Activity Monitor's Quit.
    start("--port", str(free_port()))
    # Written by the server itself (os.getpid), so it is the server's pid on every platform.
    pid = recorded(dirs).pid

    os.kill(pid, signal.SIGTERM)

    assert wait_until(lambda: not running(pid), STOP_TIMEOUT_S)
    assert "server_stopped" in logged_events(dirs)
    assert library_free(dirs)


def test_a_second_launch_opens_the_running_server(dirs: AppDirs) -> None:
    port = free_port()
    start("--port", str(port))
    first = recorded(dirs)

    out, _ = start(timeout=SECOND_LAUNCH_TIMEOUT_S)

    assert "already running" in out
    assert f"http://127.0.0.1:{port}/" in out
    assert recorded(dirs).pid == first.pid


def test_launches_at_the_same_time_share_one_server(dirs: AppDirs) -> None:
    # The Dock icon clicked twice: the second launch finds the first still starting.
    port = free_port()

    launches = [launch("--port", str(port)) for _ in range(2)]
    results = [finish(process, LAUNCH_TIMEOUT_S) for process in launches]

    assert [code for code, _, _ in results] == [0, 0], [err for _, _, err in results]
    with client(port) as http:
        assert http.get("/api/health").status_code == 200
    assert not library_free(dirs)


def test_restart_reuses_the_recorded_port(dirs: AppDirs) -> None:
    port = free_port()
    start("--port", str(port))
    stop(dirs)

    start()

    assert recorded(dirs).port == port
