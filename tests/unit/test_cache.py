import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pytest

from dental_image_organizer.storage.cache import BlobCache, cache_key, decode_array, encode_array


def _entry(cache_dir: Path, key: str) -> Path:
    return cache_dir / key[:2] / key


def test_put_get_and_miss(tmp_path: Path) -> None:
    cache = BlobCache(tmp_path, max_bytes=1000)
    key = cache_key("sha", "edit", "320")

    cache.put(key, b"jpeg-bytes")

    assert cache.get(key) == b"jpeg-bytes"
    assert cache.get(cache_key("other")) is None


def test_eviction_drops_the_least_recently_used_first(tmp_path: Path) -> None:
    cache = BlobCache(tmp_path, max_bytes=1000)
    keys = [cache_key(str(i)) for i in range(3)]
    for mtime, key in enumerate(keys, start=1):
        cache.put(key, b"x" * 400)
        os.utime(_entry(tmp_path, key), (mtime, mtime))

    assert cache.get(keys[0]) is not None  # a hit makes the oldest the newest

    assert cache.evict() == 400
    assert cache.get(keys[0]) is not None
    assert cache.get(keys[1]) is None
    assert cache.get(keys[2]) is not None


def test_eviction_skips_entries_it_cannot_delete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = BlobCache(tmp_path, max_bytes=400)
    keys = [cache_key(str(i)) for i in range(3)]
    for mtime, key in enumerate(keys, start=1):
        cache.put(key, b"x" * 400)
        os.utime(_entry(tmp_path, key), (mtime, mtime))
    locked = _entry(tmp_path, keys[0])
    real_unlink = Path.unlink

    def unlink(self: Path, missing_ok: bool = False) -> None:
        if self == locked:
            raise PermissionError("open in another thread")
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink)

    assert cache.evict() == 800
    assert locked.exists()
    assert not _entry(tmp_path, keys[1]).exists()
    assert not _entry(tmp_path, keys[2]).exists()


def test_concurrent_puts_of_one_key(tmp_path: Path) -> None:
    cache = BlobCache(tmp_path, max_bytes=10_000)
    key = cache_key("same")

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(cache.put, key, b"payload") for _ in range(8)]
        for future in futures:
            future.result()

    assert cache.get(key) == b"payload"
    assert [p.name for p in (tmp_path / key[:2]).iterdir()] == [key]


def test_arrays_round_trip_without_pickle() -> None:
    array = np.random.default_rng(0).integers(0, 256, (1200, 1600, 3), dtype=np.uint8)

    assert np.array_equal(decode_array(encode_array(array)), array)
    with pytest.raises(ValueError):
        encode_array(np.array([object()], dtype=object))


def test_clear_empties_the_cache(tmp_path: Path) -> None:
    cache = BlobCache(tmp_path, max_bytes=1000)
    keys = [cache_key(str(i)) for i in range(3)]
    for key in keys:
        cache.put(key, b"data")

    cache.clear()

    assert all(cache.get(key) is None for key in keys)
