"""BlobStore Protocol + factory + S3BlobStore behavior."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path
from typing import Any

import pytest

from sfmapi.server.core.config import Settings
from sfmapi.server.core.errors import StorageError
from sfmapi.server.storage.blobs import (
    BlobStore,
    FSBlobStore,
    S3BlobStore,
    get_blob_store,
)

pytestmark = pytest.mark.unit


def _settings(tmp_path: Path, **overrides: Any) -> Settings:
    return Settings(
        workspace_root=tmp_path / "ws",
        blob_root=tmp_path / "blobs",
        s3_cache_root=tmp_path / "s3cache",
        **overrides,
    )


def test_factory_returns_fs_by_default(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    bs = get_blob_store(s)
    assert isinstance(bs, FSBlobStore)
    assert bs.backend == "fs"
    assert isinstance(bs, BlobStore)  # Protocol runtime check


def test_factory_returns_s3_when_configured(tmp_path: Path) -> None:
    s = _settings(tmp_path, blob_backend="s3", blob_s3_bucket="my-bucket")
    bs = get_blob_store(s)
    assert isinstance(bs, S3BlobStore)
    assert bs.backend == "s3"


def test_factory_rejects_unknown_backend(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    s.blob_backend = "redis"  # type: ignore[assignment]
    with pytest.raises(StorageError, match="unknown blob_backend"):
        get_blob_store(s)


def test_s3_requires_bucket(tmp_path: Path) -> None:
    s = _settings(tmp_path, blob_backend="s3")
    with pytest.raises(StorageError, match="SFMAPI_BLOB_S3_BUCKET"):
        S3BlobStore(s)


def test_fs_local_path_raises_when_missing(tmp_path: Path) -> None:
    bs = FSBlobStore(_settings(tmp_path))
    with pytest.raises(StorageError, match="Blob missing"):
        bs.local_path("0" * 64)


# ----- S3 backend with a fake boto3 client ------------------------------


class _FakeS3Client:
    """Minimal in-memory fake of the boto3 S3 client surface used by
    S3BlobStore. Stores objects keyed by (bucket, key)."""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.calls: list[tuple[str, dict]] = []

    def put_object(self, *, Bucket: str, Key: str, Body: Any) -> dict:
        data = Body.read() if hasattr(Body, "read") else Body
        self.objects[(Bucket, Key)] = data
        self.calls.append(("put_object", {"Bucket": Bucket, "Key": Key, "size": len(data)}))
        return {"ETag": '"' + hashlib.md5(data).hexdigest() + '"'}

    def head_object(self, *, Bucket: str, Key: str) -> dict:
        if (Bucket, Key) not in self.objects:
            from botocore.exceptions import ClientError  # type: ignore[import-not-found]

            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        return {"ContentLength": len(self.objects[(Bucket, Key)])}

    def download_file(self, bucket: str, key: str, dst: str) -> None:
        if (bucket, key) not in self.objects:
            from botocore.exceptions import ClientError  # type: ignore[import-not-found]

            raise ClientError({"Error": {"Code": "404"}}, "GetObject")
        Path(dst).write_bytes(self.objects[(bucket, key)])
        self.calls.append(("download_file", {"bucket": bucket, "key": key}))

    def delete_object(self, *, Bucket: str, Key: str) -> dict:
        self.objects.pop((Bucket, Key), None)
        self.calls.append(("delete_object", {"Bucket": Bucket, "Key": Key}))
        return {}


def _s3_store(tmp_path: Path) -> tuple[S3BlobStore, _FakeS3Client]:
    boto = pytest.importorskip("botocore")  # noqa: F841 — skip if botocore absent
    s = _settings(
        tmp_path, blob_backend="s3", blob_s3_bucket="test-bucket", blob_s3_prefix="blobs/"
    )
    bs = S3BlobStore(s)
    fake = _FakeS3Client()
    bs._client = fake  # type: ignore[assignment]
    return bs, fake


def test_s3_put_then_local_path_round_trip(tmp_path: Path) -> None:
    bs, fake = _s3_store(tmp_path)
    payload = b"hello s3 blob"
    sha, n = bs.put_bytes(payload)
    assert n == len(payload)
    assert sha == hashlib.sha256(payload).hexdigest()

    # Object landed in the bucket under the configured prefix.
    expected_key = f"blobs/{sha[:2]}/{sha}"
    assert ("test-bucket", expected_key) in fake.objects

    # local_path returns the cache path with bytes intact.
    p = bs.local_path(sha)
    assert p.is_file()
    assert p.read_bytes() == payload


def test_s3_local_path_downloads_from_bucket_when_cache_missing(tmp_path: Path) -> None:
    bs, fake = _s3_store(tmp_path)
    payload = b"download me"
    sha = hashlib.sha256(payload).hexdigest()
    fake.objects[("test-bucket", f"blobs/{sha[:2]}/{sha}")] = payload

    p = bs.local_path(sha)
    assert p.read_bytes() == payload
    assert any(c[0] == "download_file" for c in fake.calls)

    # Second call hits cache; no extra download.
    fake.calls.clear()
    p2 = bs.local_path(sha)
    assert p2 == p
    assert not any(c[0] == "download_file" for c in fake.calls)


def test_s3_exists_uses_cache_then_head(tmp_path: Path) -> None:
    bs, fake = _s3_store(tmp_path)
    sha = "a" * 64
    assert bs.exists(sha) is False
    fake.objects[("test-bucket", f"blobs/{sha[:2]}/{sha}")] = b"x"
    assert bs.exists(sha) is True


def test_s3_delete_clears_both_cache_and_bucket(tmp_path: Path) -> None:
    bs, fake = _s3_store(tmp_path)
    sha, _ = bs.put_bytes(b"goodbye")
    assert bs._cache_path_for(sha).exists()
    assert ("test-bucket", f"blobs/{sha[:2]}/{sha}") in fake.objects
    bs.delete(sha)
    assert not bs._cache_path_for(sha).exists()
    assert ("test-bucket", f"blobs/{sha[:2]}/{sha}") not in fake.objects


def test_s3_put_idempotent_when_already_in_bucket(tmp_path: Path) -> None:
    bs, fake = _s3_store(tmp_path)
    payload = b"once"
    bs.put_bytes(payload)
    fake.calls.clear()
    bs.put_bytes(payload)
    # second put_bytes should not re-upload (head_object returns 200).
    assert not any(c[0] == "put_object" for c in fake.calls)


def test_invalid_sha_rejected(tmp_path: Path) -> None:
    bs = FSBlobStore(_settings(tmp_path))
    with pytest.raises(StorageError, match="Invalid sha"):
        bs.path_for("not-a-sha")


def test_aiter_chunks_yields_full_payload_fs(tmp_path: Path) -> None:
    import asyncio

    bs = FSBlobStore(_settings(tmp_path))
    payload = b"x" * (1024 * 1024 + 7)
    sha, _ = bs.put_bytes(payload)

    async def collect() -> bytes:
        out = bytearray()
        async for chunk in bs.aiter_chunks(sha, chunk_size=64 * 1024):
            out.extend(chunk)
        return bytes(out)

    assert asyncio.run(collect()) == payload


def test_put_stream_with_large_seekable_payload_fs(tmp_path: Path) -> None:
    bs = FSBlobStore(_settings(tmp_path))
    payload = b"abcdef" * 100_000
    sha, n = bs.put_stream(io.BytesIO(payload), chunk_size=8 * 1024)
    assert n == len(payload)
    assert sha == hashlib.sha256(payload).hexdigest()
    assert bs.local_path(sha).read_bytes() == payload
