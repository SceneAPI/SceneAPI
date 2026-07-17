from __future__ import annotations

import hashlib
import io
import os

import pytest

from sfmapi.server.storage.blobs import FSBlobStore, TempUploadStore

pytestmark = pytest.mark.integration


def test_put_bytes_creates_file_and_returns_sha() -> None:
    bs = FSBlobStore()
    data = b"hello sfmapi"
    sha, n = bs.put_bytes(data)
    assert n == len(data)
    assert sha == hashlib.sha256(data).hexdigest()
    p = bs.path_for(sha)
    assert p.is_file()
    assert p.read_bytes() == data


def test_put_same_bytes_idempotent() -> None:
    bs = FSBlobStore()
    sha1, _ = bs.put_bytes(b"abc")
    sha2, _ = bs.put_bytes(b"abc")
    assert sha1 == sha2


def test_put_stream_chunked() -> None:
    bs = FSBlobStore()
    payload = os.urandom(2 * 1024 * 1024 + 17)
    sha, n = bs.put_stream(io.BytesIO(payload))
    assert n == len(payload)
    assert sha == hashlib.sha256(payload).hexdigest()


def test_delete_removes_file() -> None:
    bs = FSBlobStore()
    sha, _ = bs.put_bytes(b"trash")
    bs.delete(sha)
    assert not bs.path_for(sha).exists()


def test_temp_upload_append_and_finalize() -> None:
    bs = FSBlobStore()
    temp = TempUploadStore()
    upload_id = "01HZTESTUPLOAD000000000000"
    chunk1 = b"hello "
    chunk2 = b"world!"
    n1 = temp.append(upload_id, 0, chunk1)
    n2 = temp.append(upload_id, n1, chunk2)
    assert n2 == len(chunk1) + len(chunk2)
    sha, total = temp.finalize_into(upload_id, bs)
    assert total == n2
    assert sha == hashlib.sha256(chunk1 + chunk2).hexdigest()


def test_temp_upload_rejects_out_of_order() -> None:
    from sfmapi.server.core.errors import StorageError

    temp = TempUploadStore()
    uid = "01HZTESTUPLOAD000000000001"
    temp.append(uid, 0, b"abc")
    with pytest.raises(StorageError, match="Out-of-order"):
        temp.append(uid, 99, b"xyz")
