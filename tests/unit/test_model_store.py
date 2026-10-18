import hashlib
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from dental_image_organizer.errors import ModelIntegrityError, ModelUnavailableError
from dental_image_organizer.ml.model_store import (
    DEFAULT_MODEL,
    ModelSpec,
    cached_model,
    download_model,
    sha256_file,
)


def _spec(data: bytes) -> ModelSpec:
    return ModelSpec(
        "m.onnx", "https://example.test/m.onnx", hashlib.sha256(data).hexdigest(), len(data)
    )


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_default_model_is_pinned_to_a_github_release() -> None:
    assert DEFAULT_MODEL.url.startswith(
        "https://github.com/jimwellh/Dental-Image-Organizer/releases/"
    )
    assert len(DEFAULT_MODEL.sha256) == 64 and DEFAULT_MODEL.size_bytes > 20_000_000


def test_download_verifies_reports_progress_and_caches(tmp_path: Path) -> None:
    data = b"model bytes" * 1000
    seen: list[tuple[int, int]] = []

    path = download_model(
        _spec(data),
        tmp_path,
        client=_client(lambda r: httpx.Response(200, content=data)),
        progress=lambda done, total: seen.append((done, total)),
    )

    assert path.read_bytes() == data
    assert seen[-1] == (len(data), len(data))
    assert cached_model(_spec(data), tmp_path) == path


def test_download_follows_the_release_redirect(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "example.test":
            return httpx.Response(302, headers={"location": "https://cdn.example.test/m"})
        return httpx.Response(200, content=b"x" * 10)

    assert download_model(_spec(b"x" * 10), tmp_path, client=_client(handler)).exists()


def test_tampered_download_is_rejected_and_removed(tmp_path: Path) -> None:
    with pytest.raises(ModelIntegrityError):
        download_model(
            _spec(b"good"), tmp_path, client=_client(lambda r: httpx.Response(200, content=b"evil"))
        )

    assert list(tmp_path.iterdir()) == []


def test_http_error_is_unavailable_and_leaves_nothing(tmp_path: Path) -> None:
    with pytest.raises(ModelUnavailableError):
        download_model(_spec(b"good"), tmp_path, client=_client(lambda r: httpx.Response(404)))

    assert list(tmp_path.iterdir()) == []


def test_cached_file_with_a_wrong_hash_is_deleted(tmp_path: Path) -> None:
    (tmp_path / "m.onnx").write_bytes(b"stale")

    assert cached_model(_spec(b"fresh"), tmp_path) is None
    assert not (tmp_path / "m.onnx").exists()


def test_sha256_file(tmp_path: Path) -> None:
    (tmp_path / "f").write_bytes(b"abc")

    assert sha256_file(tmp_path / "f") == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )
