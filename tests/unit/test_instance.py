from pathlib import Path

from dental_image_organizer.instance import (
    InstanceInfo,
    InstanceLock,
    read_instance,
    write_instance,
)


def test_the_lock_is_exclusive(tmp_path: Path) -> None:
    path = tmp_path / "data" / "instance.lock"
    first, second = InstanceLock(path), InstanceLock(path)

    assert first.try_acquire()
    assert first.held
    assert not second.try_acquire()
    assert not second.held

    first.release()
    assert not first.held
    assert second.try_acquire()
    second.release()


def test_instance_info_round_trips_and_tolerates_damage(tmp_path: Path) -> None:
    path = tmp_path / "instance.json"
    assert read_instance(path) is None

    info = InstanceInfo(port=8766, pid=42, version="0.3.0", token="abc")
    write_instance(path, info)
    assert read_instance(path) == info

    write_instance(path, InstanceInfo(port=8765, pid=1, version="0.3.0", token=None))
    assert read_instance(path) == InstanceInfo(8765, 1, "0.3.0", None)

    for damaged in (b"{", b"[]", b'{"port": "x", "pid": 1, "version": "v"}'):
        path.write_bytes(damaged)
        assert read_instance(path) is None
