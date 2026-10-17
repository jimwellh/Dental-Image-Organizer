"""Photo embeddings from an ONNX DINOv2 model (spec §4.6)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import numpy.typing as npt
import onnxruntime as ort
import structlog

from dental_image_organizer.core.imageio import RGBImage
from dental_image_organizer.errors import ModelUnavailableError

INPUT_NAME = "pixels"
OUTPUT_NAME = "embedding"
INPUT_SIZE = (336, 224)  # (width, height)
BLOCKS = 7
BLOCK_DIM = 384
EMBEDDING_DIM = BLOCKS * BLOCK_DIM
PHOTOS_PER_BATCH = 9
IMAGENET_MEAN = (0.485, 0.456, 0.406)
IMAGENET_STD = (0.229, 0.224, 0.225)

Embeddings = npt.NDArray[np.float32]

_log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class EmbeddingPair:
    """Embeddings of photos and of their horizontally mirrored network inputs."""

    original: Embeddings
    mirrored: Embeddings

    def __len__(self) -> int:
        return len(self.original)

    def take(self, indices: Sequence[int] | npt.NDArray[np.integer]) -> EmbeddingPair:
        idx = np.asarray(indices, dtype=np.intp)
        return EmbeddingPair(self.original[idx], self.mirrored[idx])


def preprocess(rgb: RGBImage) -> npt.NDArray[np.float32]:
    """Landscape, resized, ImageNet-normalized CHW tensor."""
    if rgb.shape[0] > rgb.shape[1]:
        # Portrait shots are rotated so every photo reaches the network landscape.
        upright = cv2.rotate(rgb, cv2.ROTATE_90_COUNTERCLOCKWISE)
    else:
        upright = rgb
    small = cv2.resize(upright, INPUT_SIZE, interpolation=cv2.INTER_AREA)
    scaled = small.astype(np.float32) / 255.0
    normalized = (scaled - np.array(IMAGENET_MEAN, np.float32)) / np.array(IMAGENET_STD, np.float32)
    return np.ascontiguousarray(normalized.transpose(2, 0, 1), dtype=np.float32)


def normalize_blocks(raw: npt.NDArray[np.floating]) -> Embeddings:
    """L2-normalize each 384-wide block so no block dominates a distance."""
    blocks = np.asarray(raw, dtype=np.float32).reshape(-1, BLOCKS, BLOCK_DIM)
    norms = np.linalg.norm(blocks, axis=2, keepdims=True)
    unit = blocks / np.maximum(norms, np.float32(1e-12))
    return unit.reshape(-1, EMBEDDING_DIM).astype(np.float32, copy=False)


class Embedder:
    def __init__(self, model_path: Path, model_hash: str) -> None:
        try:
            self._session = ort.InferenceSession(
                str(model_path), providers=["CPUExecutionProvider"]
            )
        except Exception as exc:  # onnxruntime raises its own exception types
            raise ModelUnavailableError(f"Cannot load the AI model: {exc}") from exc
        inputs = [i.name for i in self._session.get_inputs()]
        outputs = [o.name for o in self._session.get_outputs()]
        if inputs != [INPUT_NAME] or outputs != [OUTPUT_NAME]:
            raise ModelUnavailableError(
                f"Cannot load the AI model: unexpected interface {inputs} -> {outputs}"
            )
        self.model_hash = model_hash

    def embed(self, images: Sequence[RGBImage]) -> EmbeddingPair:
        originals: list[Embeddings] = []
        mirrors: list[Embeddings] = []
        for start in range(0, len(images), PHOTOS_PER_BATCH):
            chunk = images[start : start + PHOTOS_PER_BATCH]
            batch = np.stack([preprocess(img) for img in chunk])
            # One run of photos plus their mirrors halves the per-call overhead.
            both = np.concatenate([batch, np.ascontiguousarray(batch[..., ::-1])])
            out = self._session.run([OUTPUT_NAME], {INPUT_NAME: both})[0]
            if out.shape != (2 * len(chunk), EMBEDDING_DIM):
                raise ModelUnavailableError(
                    f"The AI model returned an unexpected output shape {tuple(out.shape)}"
                )
            normalized = normalize_blocks(out)
            originals.append(normalized[: len(chunk)])
            mirrors.append(normalized[len(chunk) :])
        _log.debug("embedded", photos=len(images))
        if not originals:
            empty = np.empty((0, EMBEDDING_DIM), np.float32)
            return EmbeddingPair(empty, empty)
        return EmbeddingPair(np.concatenate(originals), np.concatenate(mirrors))
