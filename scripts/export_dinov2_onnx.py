# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = [
#   "numpy==2.5.4",
#   "onnx==1.23.2",
#   "onnxruntime==1.31.0",
#   "onnxscript==0.7.2",
#   "torch==2.14.1",
#   "transformers==5.20.1",
# ]
# ///
"""Maintainer-only: export the composite embedding model (spec §4.6, §7.2).

DINOv2 ViT-S/14 (Apache-2.0) with in-graph pooling -> ONNX -> dynamic int8 quantization.
Output: (N, 2688) = the CLS token plus the mean patch token of each cell of a 3 x 2 grid,
for an input of (N, 3, 224, 336). Block L2 normalization happens in the app (ml/embedder.py).

Run with:  uv run scripts/export_dinov2_onnx.py --out dist/models
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import numpy as np
import onnxruntime as ort
import torch
from onnxruntime.quantization import QuantType, quantize_dynamic
from transformers import Dinov2Model

MODEL_ID = "facebook/dinov2-small"
# Pinned so a re-export reproduces the released file; the model card states Apache-2.0.
REVISION = "ed25f3a31f01632728cabb09d1542f84ab7b0056"
HEIGHT, WIDTH, PATCH, DIM = 224, 336, 14, 384
ROWS, COLS = HEIGHT // PATCH, WIDTH // PATCH  # 16 x 24 patches
GRID_ROWS, GRID_COLS = 2, 3  # 8 x 8 patches per cell
FP32_NAME = "dinov2s_pooled_fp32.onnx"
INT8_NAME = "dinov2s_pooled_int8.onnx"
# int8 vs fp32 on smooth inputs: per-channel weights measured min 0.982 / mean 0.990, while
# per-tensor weights measured 0.965 / 0.973 and are not used.
MIN_BLOCK_COSINE = 0.95
MIN_MEAN_COSINE = 0.98


class PooledDinov2(torch.nn.Module):
    def __init__(self, backbone: Dinov2Model) -> None:
        super().__init__()
        self.backbone = backbone

    def forward(self, pixels: torch.Tensor) -> torch.Tensor:
        tokens = self.backbone(pixel_values=pixels).last_hidden_state  # (N, 1 + 384, 384)
        n = pixels.shape[0]
        patches = tokens[:, 1:].reshape(
            n, GRID_ROWS, ROWS // GRID_ROWS, GRID_COLS, COLS // GRID_COLS, DIM
        )
        cells = patches.mean(dim=(2, 4)).reshape(n, GRID_ROWS * GRID_COLS, DIM)
        return torch.cat([tokens[:, :1], cells], dim=1).reshape(
            n, (1 + GRID_ROWS * GRID_COLS) * DIM
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    backbone = Dinov2Model.from_pretrained(MODEL_ID, revision=REVISION).eval()
    model = PooledDinov2(backbone).eval()
    fp32, int8 = args.out / FP32_NAME, args.out / INT8_NAME
    sample = torch.randn(2, 3, HEIGHT, WIDTH)
    torch.onnx.export(
        model,
        (sample,),
        str(fp32),
        input_names=["pixels"],
        output_names=["embedding"],
        dynamic_axes={"pixels": {0: "batch"}, "embedding": {0: "batch"}},
        opset_version=17,
        dynamo=False,
    )
    quantize_dynamic(str(fp32), str(int8), weight_type=QuantType.QInt8, per_channel=True)

    # Smooth random images: white noise is far from photos and exaggerates int8 error.
    coarse = torch.from_numpy(
        np.random.default_rng(0).standard_normal((4, 3, 8, 12)).astype(np.float32)
    )
    x = torch.nn.functional.interpolate(coarse, size=(HEIGHT, WIDTH), mode="bicubic").numpy()
    with torch.no_grad():
        expected = model(torch.from_numpy(x)).numpy()
    for path in (fp32, int8):
        (got,) = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"]).run(
            ["embedding"], {"pixels": x}
        )
        low, mean = _block_cosine(expected, got)
        print(
            f"{path.name}: shape {got.shape}, block cosine vs PyTorch min {low:.4f} mean {mean:.4f}"
        )
        if got.shape != (4, 2688) or low < MIN_BLOCK_COSINE or mean < MIN_MEAN_COSINE:
            raise SystemExit(f"{path.name} does not reproduce the PyTorch embeddings")

    data = int8.read_bytes()
    print(f"sha256:   {hashlib.sha256(data).hexdigest()}")
    print(f"size:     {len(data)} bytes")


def _block_cosine(a: np.ndarray, b: np.ndarray) -> tuple[float, float]:
    a, b = a.reshape(len(a), -1, DIM), b.reshape(len(b), -1, DIM)
    cosine = (a * b).sum(axis=2) / (np.linalg.norm(a, axis=2) * np.linalg.norm(b, axis=2))
    return float(cosine.min()), float(cosine.mean())


if __name__ == "__main__":
    main()
