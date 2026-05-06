"""dHash perceptual hash + similarity index unit tests."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image as PILImage

from app.storage import similarity as sim

pytestmark = pytest.mark.unit


def _img(color: tuple[int, int, int], size: int = 64) -> bytes:
    im = PILImage.new("RGB", (size, size), color=color)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def _gradient(seed: int, size: int = 64) -> bytes:
    """Per-seed gradient image so distinct seeds produce distinct hashes."""
    im = PILImage.new("RGB", (size, size))
    px = im.load()
    for x in range(size):
        for y in range(size):
            px[x, y] = (
                (x * 7 + seed * 13) % 256,
                (y * 11 + seed * 17) % 256,
                (x + y + seed * 23) % 256,
            )
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def test_dhash_is_64_bits() -> None:
    h = sim.dhash_bytes(_img((128, 128, 128)))
    assert 0 <= h < (1 << 64)


def test_dhash_hex_is_16_chars() -> None:
    s = sim.dhash_hex(_img((50, 100, 150)))
    assert len(s) == 16
    assert all(c in "0123456789abcdef" for c in s)


def test_identical_images_produce_identical_hashes() -> None:
    payload = _img((30, 60, 90))
    a = sim.dhash_hex(payload)
    b = sim.dhash_hex(payload)
    assert a == b


def test_different_images_produce_different_hashes() -> None:
    a = sim.dhash_hex(_gradient(1))
    b = sim.dhash_hex(_gradient(2))
    assert a != b
    assert sim.hamming(a, b) > 0


def test_hamming_distance_is_nonnegative_and_symmetric() -> None:
    h1 = sim.dhash_hex(_gradient(1))
    h2 = sim.dhash_hex(_gradient(2))
    assert sim.hamming(h1, h2) == sim.hamming(h2, h1)
    assert sim.hamming(h1, h1) == 0


def test_index_round_trip(tmp_path: Path) -> None:
    idx = sim.SimilarityIndex(
        strategy="dhash",
        manifest_hash="abc",
        hashes={"a": "0" * 16, "b": "ffffffffffffffff"},
    )
    sim.write_index(tmp_path, idx)
    loaded = sim.read_index(tmp_path, "dhash")
    assert loaded is not None
    assert loaded.strategy == "dhash"
    assert loaded.manifest_hash == "abc"
    assert loaded.hashes == idx.hashes


def test_k_nearest_orders_by_distance() -> None:
    idx = sim.SimilarityIndex(
        strategy="dhash",
        manifest_hash="x",
        hashes={
            "self": "0" * 16,
            "near": "0" * 15 + "1",  # hamming 1
            "mid": "0" * 8 + "f" * 8,  # hamming ~32
            "far": "f" * 16,  # hamming 64
        },
    )
    out = sim.k_nearest(idx, image_id="self", k=2)
    assert [n.image_id for n in out] == ["near", "mid"]
    assert out[0].distance == 1


def test_k_nearest_unknown_image_raises() -> None:
    idx = sim.SimilarityIndex(strategy="dhash", manifest_hash="", hashes={"a": "0" * 16})
    with pytest.raises(KeyError):
        sim.k_nearest(idx, image_id="ghost")


def test_k_nearest_include_self_returns_distance_zero() -> None:
    idx = sim.SimilarityIndex(
        strategy="dhash",
        manifest_hash="",
        hashes={"a": "1234567890abcdef", "b": "0" * 16},
    )
    out = sim.k_nearest(idx, image_id="a", k=2, include_self=True)
    assert out[0].image_id == "a"
    assert out[0].distance == 0
