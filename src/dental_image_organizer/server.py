"""`dio serve` (spec §4.3): one server per library, on a port that survives restarts, started in
the foreground or detached for the macOS launcher."""

from __future__ import annotations

import contextlib
import os
import secrets
import signal
import socket
import subprocess
import sys
import time
import webbrowser
from collections.abc import Iterator, Sequence
from typing import Any

import httpx
import structlog
import typer
import uvicorn

from dental_image_organizer import __version__
from dental_image_organizer.api.app import create_app
from dental_image_organizer.errors import DioError, StorageError
from dental_image_organizer.instance import (
    InstanceInfo,
    InstanceLock,
    read_instance,
    write_instance,
)
from dental_image_organizer.log import configure_logging
from dental_image_organizer.paths import AppDirs, app_dirs
from dental_image_organizer.services.context import create_context

log = structlog.get_logger(__name__)

PORTS = range(8765, 8776)
MIN_PORT, MAX_PORT = 1024, 65535
HEALTH_TIMEOUT_S = 20.0
READY_TOKEN_ENV = "DIO_READY_TOKEN"
POLL_S = 0.1
HOST = "127.0.0.1"


def candidate_ports(requested: int | None, previous: int | None) -> list[int]:
    """The ports to try, in order: the requested one alone, else the last run's port first, so
    an open page reconnects to the restarted server at the same origin."""
    if requested is not None:
        return [requested]
    # instance.json is a hint at most; a port no server could use must not stop the start.
    if previous is None or not MIN_PORT <= previous <= MAX_PORT:
        return list(PORTS)
    return [previous, *(port for port in PORTS if port != previous)]


def bind_first_free(ports: Sequence[int]) -> socket.socket:
    """A listening socket on the first free port. Bound here, so no other program can take the
    port between choosing it and serving on it."""
    for port in ports:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if sys.platform == "win32":
                # SO_REUSEADDR on Windows would let another program bind the same port.
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                # So a restart can bind while the last run's connections are in TIME_WAIT.
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((HOST, port))
            sock.listen(128)
        except OSError:
            sock.close()
            continue
        return sock
    if len(ports) == 1:
        raise DioError(f"Port {ports[0]} is in use.")
    raise DioError(
        f"Ports {min(ports)}\N{EN DASH}{max(ports)} are all in use; "
        "quit the program that uses them, "
        "or pass --port."
    )


def healthy(port: int) -> bool:
    try:
        # trust_env=False: an HTTP_PROXY in the environment must not route this local check.
        with httpx.Client(trust_env=False, timeout=1.0) as client:
            response = client.get(f"http://{HOST}:{port}/api/health")
    except httpx.HTTPError:
        return False
    return response.status_code == 200


def open_ui(port: int) -> None:
    webbrowser.open(f"http://{HOST}:{port}/")


def serve(*, port: int | None, open_browser: bool, verbose: bool = False) -> int:
    dirs = app_dirs()
    configure_logging("DEBUG" if verbose else "INFO", log_file=dirs.log_file)
    lock = InstanceLock(dirs.instance_lock)
    if not lock.try_acquire():
        return _defer_to_running(dirs, open_browser=open_browser)
    try:
        return _serve_locked(dirs, port=port, open_browser=open_browser)
    finally:
        lock.release()


def serve_detached(*, port: int | None, open_browser: bool, verbose: bool = False) -> int:
    dirs = app_dirs()
    configure_logging("DEBUG" if verbose else "INFO", log_file=dirs.log_file)
    lock = InstanceLock(dirs.instance_lock)
    if not lock.try_acquire():
        return _defer_to_running(dirs, open_browser=open_browser)
    # Only a probe: the child takes the lock, and would find this process holding it.
    lock.release()
    token = secrets.token_hex(16)
    # -P: `-m` would otherwise put the working directory first on sys.path, so a stray file
    # there named like a module the app imports would replace it.
    args = [sys.executable, "-P", "-m", "dental_image_organizer", "serve", "--no-browser"]
    if port is not None:
        args += ["--port", str(port)]
    if verbose:
        args.append("--verbose")
    # The launcher's `do shell script` returns only once every holder of its output pipes has
    # closed them, so the child gets none of this process's streams (spec §4.3).
    child = subprocess.Popen(
        args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        env={**os.environ, READY_TOKEN_ENV: token},
        **_detach_options(),
    )
    ready = _wait_for_child(dirs, child, token)
    if ready is None:
        typer.echo(
            f"error: The server did not start within {HEALTH_TIMEOUT_S:g} s; see {dirs.log_file}",
            err=True,
        )
        return 1
    if open_browser:
        open_ui(ready)
    return 0


def _detach_options() -> dict[str, Any]:
    if sys.platform == "win32":
        return {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _wait_for_child(dirs: AppDirs, child: subprocess.Popen[bytes], token: str) -> int | None:
    """The port of the child's server once it answers; None if it does not in time."""
    deadline = time.monotonic() + HEALTH_TIMEOUT_S
    while time.monotonic() < deadline:
        # The token, not the child's pid: on Windows the venv's python.exe is a launcher, so
        # the server runs under another pid.
        info = read_instance(dirs.instance_file)
        if info is not None and info.token == token and healthy(info.port):
            return info.port
        if child.poll() is not None:
            # Another launch won the lock, and the child deferred to that launch's server.
            info = read_instance(dirs.instance_file)
            return info.port if info is not None and healthy(info.port) else None
        time.sleep(POLL_S)
    return None


def _defer_to_running(dirs: AppDirs, *, open_browser: bool) -> int:
    """Another process owns the library: show its server instead of starting a second one."""
    port = _wait_for_recorded(dirs)
    if port is None:
        typer.echo(
            f"error: Another Dental Image Organizer process holds {dirs.instance_lock} "
            f"but does not answer; see {dirs.log_file}",
            err=True,
        )
        return 1
    if open_browser:
        open_ui(port)
    else:
        typer.echo(f"Dental Image Organizer is already running at http://{HOST}:{port}/")
    return 0


def _wait_for_recorded(dirs: AppDirs) -> int | None:
    """The recorded server's port once it answers; it may still be starting."""
    deadline = time.monotonic() + HEALTH_TIMEOUT_S
    while True:
        info = read_instance(dirs.instance_file)
        if info is not None and healthy(info.port):
            return info.port
        if time.monotonic() >= deadline:
            return None
        time.sleep(POLL_S)


def _serve_locked(dirs: AppDirs, *, port: int | None, open_browser: bool) -> int:
    # Only under the lock: create_context cleans the library's staging folders.
    try:
        ctx = create_context(dirs)
    except StorageError as exc:
        # The message goes to the user only (spec §4.13).
        log.error("startup_failed", code=exc.code)
        typer.echo(f"error: {exc}", err=True)
        return 1
    try:
        previous = read_instance(dirs.instance_file)
        ports = candidate_ports(port, previous.port if previous else None)
        try:
            sock = bind_first_free(ports)
        except DioError as exc:
            log.error("startup_failed", code="ports_in_use", ports=ports)
            typer.echo(f"error: {exc}", err=True)
            return 1
        with sock:
            bound: int = sock.getsockname()[1]
            # Kept after exit: the next run tries this port first.
            write_instance(
                dirs.instance_file,
                InstanceInfo(bound, os.getpid(), __version__, os.environ.get(READY_TOKEN_ENV)),
            )
            app = create_app(ctx, port=bound, shutdown=lambda: setattr(server, "should_exit", True))
            # log_config=None keeps uvicorn's records on the handlers configured above; the
            # access log would only add noise.
            server = uvicorn.Server(
                uvicorn.Config(app, log_config=None, access_log=False, lifespan="on")
            )
            typer.echo(
                f"Dental Image Organizer is running at http://{HOST}:{bound}/ "
                "(press Ctrl+C to stop)"
            )
            # The bound socket already queues connections, so the page may load before run.
            if open_browser:
                open_ui(bound)
            log.info("server_started", port=bound, version=__version__)
            # uvicorn re-raises Ctrl+C after its graceful shutdown; here it is the normal way out.
            with _sigterm_as_interrupt(), contextlib.suppress(KeyboardInterrupt):
                server.run(sockets=[sock])
    finally:
        ctx.close()
    log.info("server_stopped")
    return 0


@contextlib.contextmanager
def _sigterm_as_interrupt() -> Iterator[None]:
    """Let SIGTERM (logout, Activity Monitor's Quit) unwind like Ctrl+C.

    uvicorn shuts down gracefully on SIGTERM, then restores the handler it found and raises the
    signal again. The default handler would end the process inside `run`, before the running
    jobs drain and the library lock is released; this one raises KeyboardInterrupt instead.
    """
    previous = signal.signal(signal.SIGTERM, signal.default_int_handler)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)
