"""Decoded originals and what is derived from them: working copies, embeddings, references.

Working copies and embeddings live in the render cache, keyed by content, so a photo shared by
two cases is decoded once. Prepared references stay in memory, because they are only needed
while one case is being aligned.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import TYPE_CHECKING

import numpy as np
import structlog

from dental_image_organizer.core.align import PreparedReference, prepare_reference
from dental_image_organizer.core.imageio import DecodedImage, RGBImage, decode, resize_long_edge
from dental_image_organizer.ml.embedder import Embedder, EmbeddingPair
from dental_image_organizer.storage.cache import cache_key, decode_array, encode_array
from dental_image_organizer.storage.schema import PhotoRecord

if TYPE_CHECKING:
    from dental_image_organizer.services.context import AppContext

log = structlog.get_logger(__name__)

WORKING_LONG_EDGE = 1600


def decode_original(ctx: AppContext, case_id: str, photo: PhotoRecord) -> DecodedImage:
    return decode(ctx.library().original_path(case_id, photo).read_bytes())


def working_copy(ctx: AppContext, case_id: str, photo: PhotoRecord) -> RGBImage:
    cached = _cached_array(ctx, cache_key("working", photo.sha256))
    if cached is not None:
        return cached
    return remember_working(ctx, photo.sha256, decode_original(ctx, case_id, photo).rgb)


def remember_working(ctx: AppContext, sha256: str, rgb: RGBImage) -> RGBImage:
    """Cache the working copy of a photo that was just decoded anyway, and return it."""
    working, _ = resize_long_edge(rgb, WORKING_LONG_EDGE)
    _store_array(ctx, cache_key("working", sha256), working)
    return working


def photo_embedding(
    ctx: AppContext, case_id: str, photo: PhotoRecord, embedder: Embedder
) -> EmbeddingPair:
    key = cache_key("embedding", photo.sha256, embedder.model_hash)
    cached = _cached_array(ctx, key)
    if cached is not None:
        return EmbeddingPair(cached[:1], cached[1:])
    pair = embedder.embed([working_copy(ctx, case_id, photo)])
    _store_array(ctx, key, np.concatenate([pair.original, pair.mirrored]))
    return pair


def _cached_array(ctx: AppContext, key: str) -> np.ndarray | None:
    data = ctx.cache.get(key)
    if data is None:
        return None
    try:
        return decode_array(data)
    except (ValueError, EOFError):
        # A damaged entry would otherwise fail this photo until it is evicted.
        log.warning("cache_entry_unreadable")
        return None


def _store_array(ctx: AppContext, key: str, array: np.ndarray) -> None:
    try:
        ctx.cache.put(key, encode_array(array))
    except OSError as exc:
        # The cache only saves time; a full or locked cache folder must not fail the photo.
        log.warning("cache_write_failed", error=exc.strerror or type(exc).__name__)


class ReferenceCache:
    """The prepared references of the cases aligned most recently; each holds about 10 MB."""

    def __init__(self, capacity: int = 2) -> None:
        self._capacity = capacity
        self._lock = threading.Lock()
        self._entries: OrderedDict[tuple[str, str], PreparedReference] = OrderedDict()

    def get(self, ctx: AppContext, case_id: str, photo: PhotoRecord) -> PreparedReference:
        key = (case_id, photo.sha256)
        with self._lock:
            found = self._entries.get(key)
            if found is not None:
                self._entries.move_to_end(key)
                return found
        # Prepared outside the lock: decoding and preparing take about half a second, and other
        # cases' references must stay readable meanwhile.
        prepared = prepare_reference(decode_original(ctx, case_id, photo).rgb)
        with self._lock:
            # Two callers may have prepared the same reference; both get the one kept.
            kept = self._entries.setdefault(key, prepared)
            self._entries.move_to_end(key)
            while len(self._entries) > self._capacity:
                self._entries.popitem(last=False)
            return kept
