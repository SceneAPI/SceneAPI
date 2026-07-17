"""InMemoryBlobStore behavior + factory wiring."""

from __future__ import annotations

import asyncio
import hashlib
import io
from pathlib import Path

import pytest

from sceneapi.server.core.config import Settings
from sceneapi.server.core.errors import StorageError
from sceneapi.server.storage.blobs import (
    BlobStore,
    InMemoryBlobStore,
    get_blob_store,
)

pytestmark = pytest.mark.unit


def _settings(tmp_path: Path) -> Settings:
    return Settings(workspace_root=tmp_path / "ws", blob_backend="memory")


def test_factory_returns_in_memory(tmp_path: Path) -> None:
    bs = get_blob_store(_settings(tmp_path))
    assert isinstance(bs, InMemoryBlobStore)
    assert bs.backend == "memory"
    assert isinstance(bs, BlobStore)
    bs.shutdown()


def test_put_then_open_round_trip(tmp_path: Path) -> None:
    bs = InMemoryBlobStore(_settings(tmp_path))
    payload = b"hello in-memory blob"
    sha, n = bs.put_bytes(payload)
    try:
        assert n == len(payload)
        assert sha == hashlib.sha256(payload).hexdigest()
        with bs.open(sha) as fh:
            assert fh.read() == payload
    finally:
        bs.shutdown()


def test_put_stream_chunked(tmp_path: Path) -> None:
    bs = InMemoryBlobStore(_settings(tmp_path))
    try:
        payload = b"x" * (256 * 1024 + 11)
        sha, n = bs.put_stream(io.BytesIO(payload), chunk_size=8 * 1024)
        assert n == len(payload)
        assert sha == hashlib.sha256(payload).hexdigest()
        assert bs.exists(sha)
    finally:
        bs.shutdown()


def test_local_path_materializes_on_demand(tmp_path: Path) -> None:
    bs = InMemoryBlobStore(_settings(tmp_path))
    try:
        sha, _ = bs.put_bytes(b"to disk on demand")
        p = bs.local_path(sha)
        assert p.is_file()
        assert p.read_bytes() == b"to disk on demand"
        # Second call returns the same path without re-writing.
        mtime = p.stat().st_mtime
        p2 = bs.local_path(sha)
        assert p2 == p
        assert p2.stat().st_mtime == mtime
    finally:
        bs.shutdown()


def test_put_idempotent(tmp_path: Path) -> None:
    bs = InMemoryBlobStore(_settings(tmp_path))
    try:
        sha1, _ = bs.put_bytes(b"once")
        sha2, _ = bs.put_bytes(b"once")
        assert sha1 == sha2
    finally:
        bs.shutdown()


def test_aiter_chunks_streams_full_payload(tmp_path: Path) -> None:
    bs = InMemoryBlobStore(_settings(tmp_path))
    try:
        payload = b"abcdef" * 50_000
        sha, _ = bs.put_bytes(payload)

        async def collect() -> bytes:
            out = bytearray()
            async for chunk in bs.aiter_chunks(sha, chunk_size=4096):
                out.extend(chunk)
            return bytes(out)

        assert asyncio.run(collect()) == payload
    finally:
        bs.shutdown()


def test_delete_removes_bytes_and_local_file(tmp_path: Path) -> None:
    bs = InMemoryBlobStore(_settings(tmp_path))
    try:
        sha, _ = bs.put_bytes(b"trash")
        p = bs.local_path(sha)
        assert p.is_file()
        bs.delete(sha)
        assert not bs.exists(sha)
        assert not p.is_file()
    finally:
        bs.shutdown()


def test_open_missing_raises(tmp_path: Path) -> None:
    bs = InMemoryBlobStore(_settings(tmp_path))
    try:
        with pytest.raises(StorageError, match="Blob missing"):
            bs.open("0" * 64)
    finally:
        bs.shutdown()


def test_shutdown_is_idempotent(tmp_path: Path) -> None:
    bs = InMemoryBlobStore(_settings(tmp_path))
    bs.put_bytes(b"x")
    bs.shutdown()
    bs.shutdown()  # second call must not raise
