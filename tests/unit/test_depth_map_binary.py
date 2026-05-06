"""Wire tests for app.schemas.depth_map_binary."""

from __future__ import annotations

import io

import numpy as np
import pytest

from app.schemas.depth_map_binary import (
    DEPTH_MAGIC,
    HEADER_SIZE,
    NORMAL_MAGIC,
    encode_depth,
    encode_normal,
    read_header,
)

pytestmark = pytest.mark.unit


def test_depth_round_trip() -> None:
    arr = np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]], dtype="<f4")
    blob = encode_depth(3, 2, 1.0, 6.0, arr.tobytes())
    assert blob[:8] == DEPTH_MAGIC
    assert len(blob) == HEADER_SIZE + 3 * 2 * 4
    fh = io.BytesIO(blob)
    magic, version, w, h, dmin, dmax = read_header(fh)
    assert magic == DEPTH_MAGIC
    assert version == 1
    assert (w, h) == (3, 2)
    assert dmin == pytest.approx(1.0)
    assert dmax == pytest.approx(6.0)
    body = np.frombuffer(fh.read(), dtype="<f4").reshape(2, 3)
    assert np.array_equal(body, arr)


def test_normal_round_trip() -> None:
    arr = np.array([[[0, 0, 1], [0, 1, 0]], [[1, 0, 0], [0, 0, -1]]], dtype="<f4")
    blob = encode_normal(2, 2, arr.tobytes())
    assert blob[:8] == NORMAL_MAGIC
    assert len(blob) == HEADER_SIZE + 2 * 2 * 3 * 4
    fh = io.BytesIO(blob)
    magic, _, w, h, _, _ = read_header(fh)
    assert magic == NORMAL_MAGIC
    assert (w, h) == (2, 2)
    body = np.frombuffer(fh.read(), dtype="<f4").reshape(2, 2, 3)
    assert np.array_equal(body, arr)


def test_depth_rejects_wrong_pixel_buffer_length() -> None:
    with pytest.raises(ValueError, match="depth pixel buffer"):
        encode_depth(2, 2, 0.0, 1.0, b"\x00" * 8)  # need 16 bytes


def test_normal_rejects_wrong_pixel_buffer_length() -> None:
    with pytest.raises(ValueError, match="normal pixel buffer"):
        encode_normal(2, 2, b"\x00" * 16)  # need 48 bytes


def test_read_header_rejects_bad_magic() -> None:
    fh = io.BytesIO(b"\x00" * HEADER_SIZE)
    with pytest.raises(ValueError, match="bad magic"):
        read_header(fh)


def test_read_header_rejects_short_buffer() -> None:
    fh = io.BytesIO(b"\x00" * (HEADER_SIZE - 1))
    with pytest.raises(ValueError, match="short header"):
        read_header(fh)
