from __future__ import annotations

import io

import pytest

from sfmapi.server.core.hashing import (
    canonical_json,
    content_address,
    hash_dict,
    stream_sha256,
)

pytestmark = pytest.mark.unit


def test_canonical_json_stable_under_key_reorder() -> None:
    a = canonical_json({"a": 1, "b": [1, 2, 3], "c": "x"})
    b = canonical_json({"c": "x", "b": [1, 2, 3], "a": 1})
    assert a == b


def test_canonical_json_returns_bytes_no_whitespace() -> None:
    out = canonical_json({"a": 1})
    assert isinstance(out, bytes)
    assert b" " not in out


def test_content_address_sha256_lowercase_hex() -> None:
    out = content_address(b"hello world")
    assert len(out) == 64
    assert all(c in "0123456789abcdef" for c in out)


def test_content_address_iter_equals_bytes() -> None:
    a = content_address(b"hello world")
    b = content_address(iter([b"hello ", b"world"]))
    assert a == b


def test_stream_sha256_round_trip() -> None:
    data = b"abc" * 1024
    sha, n = stream_sha256(io.BytesIO(data))
    assert n == len(data)
    assert sha == content_address(data)


def test_hash_dict_independent_of_key_order() -> None:
    h1 = hash_dict({"x": 1, "y": [1, 2]})
    h2 = hash_dict({"y": [1, 2], "x": 1})
    assert h1 == h2
