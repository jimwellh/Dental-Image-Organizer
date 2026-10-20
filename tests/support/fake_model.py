"""A tiny ONNX stand-in for the DINOv2 embedder (spec §8.1).

Coarse average pooling keeps a 4 x 6 spatial layout, so mirroring the input changes the output
the way it does for the real network; the real model is far too large for tests.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper

from dental_image_organizer.ml.model_store import ModelSpec

POOL = 56
POOLED_FEATURES = 3 * (224 // POOL) * (336 // POOL)  # 72
OUTPUT_DIM = 2688


def build_fake_model(path: Path, seed: int = 0) -> str:
    """Write the fake model to `path` and return its SHA-256."""
    weights = np.random.default_rng(seed).standard_normal((POOLED_FEATURES, OUTPUT_DIM))
    nodes = [
        helper.make_node(
            "AveragePool", ["pixels"], ["pooled"], kernel_shape=[POOL, POOL], strides=[POOL, POOL]
        ),
        helper.make_node("Flatten", ["pooled"], ["flat"], axis=1),
        helper.make_node("MatMul", ["flat", "weights"], ["embedding"]),
    ]
    graph = helper.make_graph(
        nodes,
        "fake_embedder",
        [helper.make_tensor_value_info("pixels", TensorProto.FLOAT, ["batch", 3, 224, 336])],
        [helper.make_tensor_value_info("embedding", TensorProto.FLOAT, ["batch", OUTPUT_DIM])],
        [numpy_helper.from_array(weights.astype(np.float32), "weights")],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)])
    model.ir_version = 8
    onnx.checker.check_model(model)
    path.write_bytes(model.SerializeToString())
    return hashlib.sha256(path.read_bytes()).hexdigest()


def install_fake_model(models_dir: Path, seed: int = 0) -> ModelSpec:
    """Write the fake model into a model cache folder and return the spec that verifies it."""
    models_dir.mkdir(parents=True, exist_ok=True)
    path = models_dir / "fake.onnx"
    sha = build_fake_model(path, seed)
    return ModelSpec("fake.onnx", "https://unused.test", sha, path.stat().st_size)
