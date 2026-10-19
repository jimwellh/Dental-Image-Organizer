import hashlib
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from structlog.testing import capture_logs

from dental_image_organizer.errors import ModelIntegrityError, ModelUnavailableError
from dental_image_organizer.ml import model_store
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


def test_a_local_write_error_is_unavailable_and_names_no_path(tmp_path: Path) -> None:
    # A file where the cache folder belongs makes mkdir fail, as a read-only home would.
    blocked = tmp_path / "Alex Wang"
    blocked.write_bytes(b"")

    with capture_logs() as logs, pytest.raises(ModelUnavailableError) as info:
        download_model(
            _spec(b"x"), blocked, client=_client(lambda r: httpx.Response(200, content=b"x"))
        )

    assert str(info.value).startswith("Saving the AI model failed: ")
    assert "Alex Wang" not in str(info.value)
    assert [e["event"] for e in logs if e["log_level"] == "error"] == ["model_download_failed"]


@pytest.mark.parametrize(
    ("name", "value"),
    [
        # Without socksio, as a proxy tool's exported shell settings leave it.
        ("ALL_PROXY", "socks5://user:secret@127.0.0.1:9"),
        ("ALL_PROXY", "ftp://user:secret@127.0.0.1:21"),
        ("SSL_CERT_FILE", "missing-secret.pem"),
    ],
)
def test_unusable_network_settings_are_unavailable_and_not_shown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "SSL_CERT_FILE"):
        monkeypatch.delenv(var, raising=False)
        monkeypatch.delenv(var.lower(), raising=False)
    monkeypatch.setenv(name, str(tmp_path / value) if name == "SSL_CERT_FILE" else value)

    with capture_logs() as logs, pytest.raises(ModelUnavailableError) as info:
        download_model(_spec(b"x"), tmp_path / "cache")

    assert str(info.value).startswith("The model could not be downloaded")
    assert "secret" not in str(info.value)
    assert "secret" not in repr(logs)
    assert not (tmp_path / "cache").exists()


def test_cached_file_with_a_wrong_hash_is_deleted(tmp_path: Path) -> None:
    (tmp_path / "m.onnx").write_bytes(b"stale")

    assert cached_model(_spec(b"fresh"), tmp_path) is None
    assert not (tmp_path / "m.onnx").exists()


def test_an_unreadable_cached_file_is_unavailable_and_names_no_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "Alex Wang"
    cache.mkdir()
    (cache / "m.onnx").write_bytes(b"model")

    def locked(path: Path) -> str:
        # Another process holding the file open on Windows, or a failing disk.
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(model_store, "sha256_file", locked)

    with pytest.raises(ModelUnavailableError) as info:
        cached_model(_spec(b"model"), cache)

    assert str(info.value) == "Reading the cached AI model failed: Permission denied"


def test_sha256_file(tmp_path: Path) -> None:
    (tmp_path / "f").write_bytes(b"abc")

    assert sha256_file(tmp_path / "f") == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


def test_oversized_download_is_aborted_before_exceeding_the_pinned_size(tmp_path: Path) -> None:
    spec = _spec(b"x" * 10)
    seen: list[int] = []

    with pytest.raises(ModelIntegrityError):
        download_model(
            spec,
            tmp_path,
            client=_client(lambda r: httpx.Response(200, content=b"x" * 5_000_000)),
            progress=lambda done, total: seen.append(done),
        )

    assert list(tmp_path.iterdir()) == []
    assert all(done <= spec.size_bytes for done in seen)
