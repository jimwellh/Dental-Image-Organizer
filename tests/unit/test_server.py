import signal
import socket
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dental_image_organizer import server
from dental_image_organizer.cli import app
from dental_image_organizer.errors import DioError
from dental_image_organizer.instance import InstanceInfo, InstanceLock, write_instance
from dental_image_organizer.paths import APP_DIR_ENV, AppDirs, app_dirs

DEFAULT_PORTS = list(range(8765, 8776))


def free_ports(count: int) -> list[int]:
    # Bound together, so the OS hands out distinct ports.
    socks = [socket.socket() for _ in range(count)]
    try:
        for sock in socks:
            sock.bind(("127.0.0.1", 0))
        return [sock.getsockname()[1] for sock in socks]
    finally:
        for sock in socks:
            sock.close()


@pytest.fixture
def dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AppDirs:
    monkeypatch.setenv(APP_DIR_ENV, str(tmp_path / "app"))
    return app_dirs()


def refuse_to_start(*_args: object, **_kwargs: object) -> None:
    raise AssertionError("a second server must never start")


def test_candidate_ports() -> None:
    assert server.candidate_ports(None, None) == DEFAULT_PORTS
    previous = server.candidate_ports(None, 8770)
    assert previous[0] == 8770
    assert sorted(previous) == DEFAULT_PORTS
    assert server.candidate_ports(None, 9000) == [9000, *DEFAULT_PORTS]
    assert server.candidate_ports(8800, 8770) == [8800]


def test_candidate_ports_ignore_a_damaged_record() -> None:
    # instance.json is a hint at most: an impossible port in it must not stop the start.
    assert server.candidate_ports(None, 0) == DEFAULT_PORTS
    assert server.candidate_ports(None, 70000) == DEFAULT_PORTS


def test_bind_first_free_skips_a_busy_port() -> None:
    busy, free = free_ports(2)
    with (
        socket.create_server(("127.0.0.1", busy)),
        server.bind_first_free([busy, free]) as sock,
    ):
        assert sock.getsockname() == ("127.0.0.1", free)


def test_bind_first_free_reports_when_all_are_busy() -> None:
    first, second = sorted(free_ports(2))
    with socket.create_server(("127.0.0.1", first)), socket.create_server(("127.0.0.1", second)):
        with pytest.raises(DioError) as scan:
            server.bind_first_free([first, second])
        with pytest.raises(DioError) as single:
            server.bind_first_free([second])

    assert str(scan.value) == (
        f"Ports {first}\N{EN DASH}{second} are all in use; "
        "quit the program that uses them, or pass --port."
    )
    assert str(single.value) == f"Port {second} is in use."


def test_serve_opens_the_running_instance_instead_of_starting(
    dirs: AppDirs, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = InstanceLock(dirs.instance_lock)
    assert lock.try_acquire()
    write_instance(dirs.instance_file, InstanceInfo(port=8799, pid=1, version="0.0.0", token=None))
    opened: list[int] = []
    monkeypatch.setattr(server, "healthy", lambda port: port == 8799)
    monkeypatch.setattr(server, "open_ui", opened.append)
    monkeypatch.setattr(server, "create_context", refuse_to_start)

    try:
        assert server.serve(port=None, open_browser=True) == 0
    finally:
        lock.release()

    assert opened == [8799]


def test_serve_reports_a_holder_that_does_not_answer(
    dirs: AppDirs, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    lock = InstanceLock(dirs.instance_lock)
    assert lock.try_acquire()
    monkeypatch.setattr(server, "HEALTH_TIMEOUT_S", 0.3)
    monkeypatch.setattr(server, "healthy", lambda port: False)
    monkeypatch.setattr(server, "create_context", refuse_to_start)

    try:
        assert server.serve(port=None, open_browser=False) == 1
    finally:
        lock.release()

    assert (
        f"error: Another Dental Image Organizer process holds {dirs.instance_lock} "
        f"but does not answer; see {dirs.log_file}"
    ) in capsys.readouterr().err


def test_serve_refuses_a_privileged_port() -> None:
    result = CliRunner().invoke(app, ["serve", "--port", "80", "--no-browser"])

    assert result.exit_code == 2


def test_sigterm_unwinds_like_ctrl_c_only_while_serving() -> None:
    # The process test sends a real SIGTERM (POSIX only); this pins that the handler is put
    # back, so an in-process caller keeps its own.
    before = signal.getsignal(signal.SIGTERM)

    with server._sigterm_as_interrupt():
        assert signal.getsignal(signal.SIGTERM) is signal.default_int_handler

    assert signal.getsignal(signal.SIGTERM) == before
