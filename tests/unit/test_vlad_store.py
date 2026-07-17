"""VLAD storage round-trip + cosine query."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from sfmapi.server.storage import vlad

pytestmark = pytest.mark.unit


def _orthogonal_vectors(n: int, dim: int) -> np.ndarray:
    """Return n orthonormal vectors in `dim`-d (n <= dim)."""
    rng = np.random.default_rng(0)
    raw = rng.standard_normal((n, dim)).astype(np.float32)
    # Gram-Schmidt
    out = np.zeros_like(raw)
    for i in range(n):
        v = raw[i].copy()
        for j in range(i):
            v -= np.dot(v, out[j]) * out[j]
        v /= np.linalg.norm(v)
        out[i] = v
    return out


def test_round_trip_preserves_normalization(tmp_path: Path) -> None:
    vecs = _orthogonal_vectors(3, 8) * 5.0  # not normalized at write time
    vlad.write_index(tmp_path, image_ids=["a", "b", "c"], vectors=vecs, manifest_hash="h1")
    idx = vlad.read_index(tmp_path)
    assert idx is not None
    assert idx.image_ids == ["a", "b", "c"]
    assert idx.manifest_hash == "h1"
    assert idx.dim == 8
    norms = np.linalg.norm(idx.vectors, axis=1)
    assert np.allclose(norms, 1.0, atol=1e-5)


def test_read_returns_none_when_missing(tmp_path: Path) -> None:
    assert vlad.read_index(tmp_path) is None


def test_orthogonal_vectors_have_distance_one(tmp_path: Path) -> None:
    vecs = _orthogonal_vectors(3, 8)
    vlad.write_index(tmp_path, image_ids=["a", "b", "c"], vectors=vecs, manifest_hash="")
    idx = vlad.read_index(tmp_path)
    assert idx is not None
    out = vlad.k_nearest(idx, image_id="a", k=2)
    assert [n.image_id for n in out] == ["b", "c"]
    for n in out:
        assert n.distance == pytest.approx(1.0, abs=1e-5)


def test_identical_vectors_distance_zero(tmp_path: Path) -> None:
    v = np.array([[1.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float32)
    vlad.write_index(tmp_path, image_ids=["a", "dup_a", "b"], vectors=v, manifest_hash="")
    idx = vlad.read_index(tmp_path)
    assert idx is not None
    out = vlad.k_nearest(idx, image_id="a", k=1)
    assert out[0].image_id == "dup_a"
    assert out[0].distance == pytest.approx(0.0, abs=1e-6)


def test_unknown_image_id_raises(tmp_path: Path) -> None:
    v = np.array([[1.0, 0.0]], dtype=np.float32)
    vlad.write_index(tmp_path, image_ids=["only"], vectors=v, manifest_hash="")
    idx = vlad.read_index(tmp_path)
    assert idx is not None
    with pytest.raises(KeyError):
        vlad.k_nearest(idx, image_id="ghost", k=3)


def test_include_self_returns_query_first(tmp_path: Path) -> None:
    v = _orthogonal_vectors(2, 4)
    vlad.write_index(tmp_path, image_ids=["a", "b"], vectors=v, manifest_hash="")
    idx = vlad.read_index(tmp_path)
    assert idx is not None
    out = vlad.k_nearest(idx, image_id="a", k=2, include_self=True)
    assert out[0].image_id == "a"
    assert out[0].distance == pytest.approx(0.0, abs=1e-6)


def test_write_rejects_shape_mismatch(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="doesn't match"):
        vlad.write_index(
            tmp_path,
            image_ids=["a", "b"],
            vectors=np.array([[1.0, 0.0]], dtype=np.float32),  # only 1 row
            manifest_hash="",
        )
