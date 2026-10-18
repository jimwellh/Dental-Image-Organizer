"""The pinned embedding model: cache lookup and the one consented download (spec §4.6)."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx
import structlog

from dental_image_organizer.errors import ModelIntegrityError, ModelUnavailableError

CHUNK_BYTES = 1 << 20
TIMEOUT_SECONDS = 60.0

_log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class ModelSpec:
    filename: str
    url: str
    sha256: str
    size_bytes: int


DEFAULT_MODEL = ModelSpec(
    "dinov2s_pooled_int8.onnx",
    "https://github.com/jimwellh/Dental-Image-Organizer/releases/download/"
    "model-dinov2s-v1/dinov2s_pooled_int8.onnx",
    "da14f66b5e204cce7c8f2b65fd2936b02d5f4cc015687f05465de32717ecc5b0",
    24_421_288,
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def cached_model(spec: ModelSpec, cache_dir: Path) -> Path | None:
    """Return the cached model if it verifies; delete a corrupt file so it is re-fetched."""
    path = cache_dir / spec.filename
    if not path.is_file():
        return None
    if path.stat().st_size == spec.size_bytes and sha256_file(path) == spec.sha256:
        return path
    _log.warning("model_cache_invalid")
    path.unlink(missing_ok=True)
    return None


def download_model(
    spec: ModelSpec,
    cache_dir: Path,
    *,
    client: httpx.Client | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> Path:
    """Download into ``cache_dir`` and publish atomically only after hash and size verify."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    final = cache_dir / spec.filename
    partial = cache_dir / f"{spec.filename}.part"
    # GitHub redirects release assets to its CDN, so redirects must be followed.
    http = client or httpx.Client(follow_redirects=True, timeout=TIMEOUT_SECONDS)
    _log.info("model_download_started")
    try:
        digest = hashlib.sha256()
        done = 0
        with http.stream("GET", spec.url) as response:
            response.raise_for_status()
            with partial.open("wb") as handle:
                for chunk in response.iter_bytes(CHUNK_BYTES):
                    # Check before writing so a hostile server cannot fill the disk.
                    if done + len(chunk) > spec.size_bytes:
                        _log.error("model_download_too_large")
                        raise ModelIntegrityError("Download is larger than the pinned model size")
                    handle.write(chunk)
                    digest.update(chunk)
                    done += len(chunk)
                    if progress is not None:
                        progress(done, spec.size_bytes)
        if done != spec.size_bytes or digest.hexdigest() != spec.sha256:
            _log.error("model_download_integrity_failed")
            raise ModelIntegrityError("Downloaded model failed its integrity check")
        os.replace(partial, final)
    except httpx.HTTPError as exc:
        _log.error("model_download_failed", error=type(exc).__name__)
        raise ModelUnavailableError("The model could not be downloaded") from exc
    finally:
        partial.unlink(missing_ok=True)
        if client is None:
            http.close()
    _log.info("model_download_finished")
    return final
