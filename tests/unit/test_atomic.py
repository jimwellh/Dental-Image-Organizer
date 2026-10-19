import os
from pathlib import Path

import pytest

from dental_image_organizer.storage import atomic


def _tmp_files(folder: Path) -> list[Path]:
    return list(folder.glob("*.tmp")) + list(folder.glob(".*.tmp"))


def test_write_replaces_the_file(tmp_path: Path) -> None:
    target = tmp_path / "sub" / "a.json"
    atomic.write_atomic(target, b"old")
    atomic.write_atomic(target, b"new")

    assert target.read_bytes() == b"new"
    assert _tmp_files(target.parent) == []


def test_a_crash_before_the_rename_keeps_the_old_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "a.json"
    target.write_bytes(b"old")

    def boom(fd: int) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "fsync", boom)

    with pytest.raises(OSError, match="disk full"):
        atomic.write_atomic(target, b"new")

    assert target.read_bytes() == b"old"
    assert _tmp_files(tmp_path) == []


def test_replace_is_retried_while_the_target_is_held(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "a.json"
    target.write_bytes(b"old")
    real_replace = os.replace
    calls = {"n": 0}

    def flaky(src: Path, dst: Path) -> None:
        calls["n"] += 1
        if calls["n"] <= 2:
            raise PermissionError("held")
        real_replace(src, dst)

    monkeypatch.setattr(atomic, "REPLACE_DELAY_S", 0)
    monkeypatch.setattr(os, "replace", flaky)

    atomic.write_atomic(target, b"new")

    assert target.read_bytes() == b"new"
    assert calls["n"] == 3


def test_replace_gives_up_after_the_last_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "a.json"
    target.write_bytes(b"old")
    calls = {"n": 0}

    def held(src: Path, dst: Path) -> None:
        calls["n"] += 1
        raise PermissionError("held")

    monkeypatch.setattr(atomic, "REPLACE_DELAY_S", 0)
    monkeypatch.setattr(os, "replace", held)

    with pytest.raises(PermissionError):
        atomic.write_atomic(target, b"new")

    assert calls["n"] == atomic.REPLACE_ATTEMPTS
    assert target.read_bytes() == b"old"
    assert _tmp_files(tmp_path) == []
