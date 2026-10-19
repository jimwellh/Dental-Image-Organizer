from pathlib import Path

import numpy as np
import pytest

from dental_image_organizer.core.slots import SLOTS
from dental_image_organizer.errors import ModelUnavailableError
from dental_image_organizer.ml.embedder import (
    BLOCK_DIM,
    EMBEDDING_DIM,
    Embedder,
    normalize_blocks,
    preprocess,
)
from support.fake_model import build_fake_model
from support.views import make_view


@pytest.fixture(scope="module")
def fake_model(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, str]:
    path = tmp_path_factory.mktemp("model") / "fake.onnx"
    return path, build_fake_model(path)


def test_preprocess_makes_a_normalized_landscape_tensor() -> None:
    portrait = np.zeros((300, 200, 3), np.uint8)
    portrait[:10, :] = 255  # top rows white

    x = preprocess(portrait)

    assert x.shape == (3, 224, 336)
    # Rotated counterclockwise, the top edge becomes the left edge.
    assert x[0, :, :5].mean() == pytest.approx((1.0 - 0.485) / 0.229, abs=0.05)
    assert x[0, :, -5:].mean() == pytest.approx((0.0 - 0.485) / 0.229, abs=0.05)


def test_each_block_is_unit_length() -> None:
    raw = np.random.default_rng(0).standard_normal((4, EMBEDDING_DIM))

    norms = np.linalg.norm(normalize_blocks(raw).reshape(4, -1, BLOCK_DIM), axis=2)

    np.testing.assert_allclose(norms, 1.0, rtol=1e-5)


@pytest.mark.parametrize("portrait", [False, True])
def test_mirror_embedding_equals_embedding_of_the_mirrored_photo(
    fake_model: tuple[Path, str], portrait: bool
) -> None:
    embedder = Embedder(*fake_model)
    views = [make_view(s, 1) for s in SLOTS] + [
        make_view("upper_right", 2),
        make_view("lower_left", 2),
    ]
    # Portrait photos reach the network turned a quarter turn, which moves their left-right axis.
    photos = [np.ascontiguousarray(np.rot90(v, k=-1)) for v in views] if portrait else views

    pair = embedder.embed(photos)  # 11 photos: more than one batch
    mirrored = embedder.embed([np.ascontiguousarray(p[:, ::-1]) for p in photos])

    assert pair.original.shape == pair.mirrored.shape == (11, EMBEDDING_DIM)
    np.testing.assert_allclose(pair.mirrored, mirrored.original, atol=1e-4)


def test_a_file_that_is_not_the_model_is_unavailable(tmp_path: Path) -> None:
    bad = tmp_path / "bad.onnx"
    bad.write_bytes(b"not an onnx model")

    with pytest.raises(ModelUnavailableError):
        Embedder(bad, "0" * 64)
