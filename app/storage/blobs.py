"""Content-addressed blob store — pluggable backends.

`BlobStore` is a Protocol; `get_blob_store()` returns the concrete
implementation chosen by ``settings.blob_backend``:

  - ``fs`` (default): bytes live at
    ``<blob_root>/<sha[:2]>/<sha>``. Writes are atomic via
    ``os.replace`` from a sibling temp file. Reads stream the file
    directly. This is the v0 backend and the test default.

  - ``s3``: bytes live at ``s3://<bucket>/<prefix><sha[:2]>/<sha>``.
    Reads download lazily to the local S3 cache and return paths into
    that cache, so callers that need a filesystem path (pycolmap, Pillow)
    work transparently. Configured via ``settings.blob_s3_*``.

Refcounting is the caller's responsibility (typically a service) inside
a transaction so blob lifecycle stays consistent with referencing rows
— that contract is identical across backends.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import stat
import tempfile
from collections.abc import AsyncIterator, Iterable
from pathlib import Path
from typing import Any, BinaryIO, Protocol, runtime_checkable

from app.core.config import Settings, get_settings
from app.core.errors import StorageError


@runtime_checkable
class BlobStore(Protocol):
    """Sha256-keyed binary store. All methods are sha-addressed; the
    backend chooses where bytes physically live."""

    def exists(self, sha: str) -> bool: ...

    def put_stream(self, reader: BinaryIO, *, chunk_size: int = ...) -> tuple[str, int]: ...

    def put_bytes(self, data: bytes) -> tuple[str, int]: ...

    def open(self, sha: str) -> BinaryIO: ...

    def aiter_chunks(self, sha: str, *, chunk_size: int = ...) -> AsyncIterator[bytes]: ...

    def delete(self, sha: str) -> None: ...

    def local_path(self, sha: str) -> Path:
        """Return a local filesystem path for the blob's bytes.

        For filesystem backends this is the canonical storage path.
        For remote backends (S3) the bytes are downloaded into the
        local cache on first access; subsequent calls return the
        cached path. Callers that need to hand a real path to a native
        library (pycolmap, Pillow, OpenCV) should use this.
        """
        ...


# --------------------------------------------------------------------
#  Filesystem backend
# --------------------------------------------------------------------


def _validate_sha(sha: str) -> None:
    if len(sha) != 64 or not all(c in "0123456789abcdef" for c in sha):
        raise StorageError(f"Invalid sha: {sha!r}")


class FSBlobStore:
    """Default backend — bytes on the local filesystem under
    ``<blob_root>/<sha[:2]>/<sha>``."""

    backend: str = "fs"
    is_singleton: bool = False

    def __init__(self, settings: Settings | None = None) -> None:
        self.s = settings or get_settings()
        self.root: Path = Path(self.s.blob_root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, sha: str) -> Path:
        _validate_sha(sha)
        return self.root / sha[:2] / sha

    # `local_path` is the cross-backend name; on FS it's the canonical path.
    def local_path(self, sha: str) -> Path:
        p = self.path_for(sha)
        if not p.is_file():
            raise StorageError(f"Blob missing: {sha}")
        return p

    def exists(self, sha: str) -> bool:
        return self.path_for(sha).is_file()

    def put_stream(self, reader: BinaryIO, *, chunk_size: int = 1024 * 1024) -> tuple[str, int]:
        h = hashlib.sha256()
        total = 0
        fd, tmp_path = tempfile.mkstemp(prefix="blob.", suffix=".tmp", dir=self.root)
        try:
            with os.fdopen(fd, "wb") as out:
                while True:
                    chunk = reader.read(chunk_size)
                    if not chunk:
                        break
                    out.write(chunk)
                    h.update(chunk)
                    total += len(chunk)
            sha = h.hexdigest()
            target = self.path_for(sha)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                os.remove(tmp_path)
            else:
                os.replace(tmp_path, target)
                with contextlib.suppress(OSError):
                    os.chmod(target, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
            return sha, total
        except Exception:
            with contextlib.suppress(OSError):
                os.remove(tmp_path)
            raise

    def put_bytes(self, data: bytes) -> tuple[str, int]:
        import io

        return self.put_stream(io.BytesIO(data))

    def open(self, sha: str) -> BinaryIO:
        return self.local_path(sha).open("rb")

    async def aiter_chunks(
        self, sha: str, *, chunk_size: int = 1024 * 1024
    ) -> AsyncIterator[bytes]:
        p = self.local_path(sha)
        with p.open("rb") as fh:
            while True:
                chunk = fh.read(chunk_size)
                if not chunk:
                    return
                yield chunk

    def delete(self, sha: str) -> None:
        p = self.path_for(sha)
        try:
            with contextlib.suppress(OSError):
                os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)
            p.unlink()
        except FileNotFoundError:
            pass
        with contextlib.suppress(OSError):
            p.parent.rmdir()


# --------------------------------------------------------------------
#  S3 backend
# --------------------------------------------------------------------


class S3BlobStore:
    """Bytes live at ``s3://<bucket>/<prefix><sha[:2]>/<sha>``.

    Reads download lazily into ``<workspace>/_blob_cache/<sha[:2]>/<sha>``
    so ``local_path()`` is a true filesystem path. The cache is content-
    addressed by sha so it never goes stale; deletes on the bucket are
    mirrored locally on next ``delete()``.
    """

    backend: str = "s3"
    is_singleton: bool = False

    def __init__(self, settings: Settings | None = None) -> None:
        self.s = settings or get_settings()
        if not self.s.blob_s3_bucket:
            raise StorageError("blob_backend=s3 requires SFMAPI_BLOB_S3_BUCKET")
        self.bucket: str = self.s.blob_s3_bucket
        self.prefix: str = (self.s.blob_s3_prefix or "").lstrip("/")
        if self.prefix and not self.prefix.endswith("/"):
            self.prefix += "/"
        self.cache_root: Path = Path(self.s.workspace_root) / "_blob_cache"
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self._client: Any | None = None

    def _s3(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import boto3  # type: ignore[import-not-found]
        except ImportError as e:
            raise StorageError("blob_backend=s3 requires boto3 (`uv pip install boto3`)") from e
        kwargs: dict[str, Any] = {}
        if self.s.blob_s3_region:
            kwargs["region_name"] = self.s.blob_s3_region
        if self.s.blob_s3_endpoint_url:
            kwargs["endpoint_url"] = self.s.blob_s3_endpoint_url
        self._client = boto3.client("s3", **kwargs)
        return self._client

    def _key_for(self, sha: str) -> str:
        return f"{self.prefix}{sha[:2]}/{sha}"

    def _cache_path_for(self, sha: str) -> Path:
        return self.cache_root / sha[:2] / sha

    def exists(self, sha: str) -> bool:
        _validate_sha(sha)
        if self._cache_path_for(sha).is_file():
            return True
        return self._exists_in_bucket(sha)

    def _exists_in_bucket(self, sha: str) -> bool:
        try:
            self._s3().head_object(Bucket=self.bucket, Key=self._key_for(sha))
            return True
        except Exception as e:
            # boto3 raises ClientError with 404 for missing objects.
            from botocore.exceptions import ClientError  # type: ignore[import-not-found]

            if isinstance(e, ClientError) and e.response.get("Error", {}).get("Code") in (
                "404",
                "NoSuchKey",
                "NotFound",
            ):
                return False
            raise

    def put_stream(self, reader: BinaryIO, *, chunk_size: int = 1024 * 1024) -> tuple[str, int]:
        # Stream into a temp file while hashing; on completion, upload
        # if not present, and move into the local cache regardless.
        h = hashlib.sha256()
        total = 0
        fd, tmp_path = tempfile.mkstemp(prefix="s3blob.", suffix=".tmp", dir=self.cache_root)
        tmp = Path(tmp_path)
        try:
            with os.fdopen(fd, "wb") as out:
                while True:
                    chunk = reader.read(chunk_size)
                    if not chunk:
                        break
                    out.write(chunk)
                    h.update(chunk)
                    total += len(chunk)
            sha = h.hexdigest()
            cache_target = self._cache_path_for(sha)
            cache_target.parent.mkdir(parents=True, exist_ok=True)
            if not cache_target.exists():
                os.replace(tmp, cache_target)
            else:
                tmp.unlink(missing_ok=True)
            # Cache hit doesn't imply bucket presence — query the bucket
            # directly to decide whether to skip the upload.
            if not self._exists_in_bucket(sha):
                with cache_target.open("rb") as fh:
                    self._s3().put_object(Bucket=self.bucket, Key=self._key_for(sha), Body=fh)
            return sha, total
        except Exception:
            tmp.unlink(missing_ok=True)
            raise

    def put_bytes(self, data: bytes) -> tuple[str, int]:
        import io

        return self.put_stream(io.BytesIO(data))

    def local_path(self, sha: str) -> Path:
        _validate_sha(sha)
        cached = self._cache_path_for(sha)
        if cached.is_file():
            return cached
        cached.parent.mkdir(parents=True, exist_ok=True)
        tmp = cached.with_suffix(".tmp")
        try:
            self._s3().download_file(self.bucket, self._key_for(sha), str(tmp))
            os.replace(tmp, cached)
        except Exception as e:
            with contextlib.suppress(FileNotFoundError):
                tmp.unlink()
            raise StorageError(f"Blob missing: {sha} ({e})") from e
        return cached

    def open(self, sha: str) -> BinaryIO:
        return self.local_path(sha).open("rb")

    async def aiter_chunks(
        self, sha: str, *, chunk_size: int = 1024 * 1024
    ) -> AsyncIterator[bytes]:
        p = self.local_path(sha)
        with p.open("rb") as fh:
            while True:
                chunk = fh.read(chunk_size)
                if not chunk:
                    return
                yield chunk

    def delete(self, sha: str) -> None:
        _validate_sha(sha)
        cached = self._cache_path_for(sha)
        with contextlib.suppress(FileNotFoundError):
            cached.unlink()
        with contextlib.suppress(OSError):
            cached.parent.rmdir()
        with contextlib.suppress(Exception):
            self._s3().delete_object(Bucket=self.bucket, Key=self._key_for(sha))


# --------------------------------------------------------------------
#  In-memory backend (ephemeral mode)
# --------------------------------------------------------------------


class InMemoryBlobStore:
    """All bytes live in a process-local dict.

    Used by ``ephemeral=true`` mode: zero disk persistence, ideal for
    tests, demos, and embedded use. Callers that need a real
    filesystem path (pycolmap, Pillow) trigger lazy materialization into
    a per-store temp dir via ``local_path()``; that materialization
    survives until ``shutdown()`` is called.

    Marked ``is_singleton = True`` so the factory caches a single
    instance per process — bytes live in ``self._bytes`` and would
    be unreachable to a writer-via-call-1 / reader-via-call-2 pattern
    if the factory built a fresh instance each time. The ``L15``
    regression-guard in the contract suite pins this invariant.
    """

    backend: str = "memory"
    is_singleton: bool = True

    def __init__(self, settings: Settings | None = None) -> None:
        self.s = settings or get_settings()
        self._bytes: dict[str, bytes] = {}
        self._scratch: Path = Path(tempfile.mkdtemp(prefix="sfmapi-mem-blobs."))

    def exists(self, sha: str) -> bool:
        _validate_sha(sha)
        return sha in self._bytes

    def put_stream(self, reader: BinaryIO, *, chunk_size: int = 1024 * 1024) -> tuple[str, int]:
        h = hashlib.sha256()
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = reader.read(chunk_size)
            if not chunk:
                break
            chunks.append(chunk)
            h.update(chunk)
            total += len(chunk)
        sha = h.hexdigest()
        if sha not in self._bytes:
            self._bytes[sha] = b"".join(chunks)
        return sha, total

    def put_bytes(self, data: bytes) -> tuple[str, int]:
        sha = hashlib.sha256(data).hexdigest()
        if sha not in self._bytes:
            self._bytes[sha] = data
        return sha, len(data)

    def open(self, sha: str) -> BinaryIO:
        if sha not in self._bytes:
            raise StorageError(f"Blob missing: {sha}")
        import io

        return io.BytesIO(self._bytes[sha])

    async def aiter_chunks(
        self, sha: str, *, chunk_size: int = 1024 * 1024
    ) -> AsyncIterator[bytes]:
        if sha not in self._bytes:
            raise StorageError(f"Blob missing: {sha}")
        data = self._bytes[sha]
        for i in range(0, len(data), chunk_size):
            yield data[i : i + chunk_size]

    def local_path(self, sha: str) -> Path:
        _validate_sha(sha)
        if sha not in self._bytes:
            raise StorageError(f"Blob missing: {sha}")
        p = self._scratch / sha[:2] / sha
        if not p.is_file():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(self._bytes[sha])
        return p

    def delete(self, sha: str) -> None:
        self._bytes.pop(sha, None)
        with contextlib.suppress(FileNotFoundError):
            (self._scratch / sha[:2] / sha).unlink()

    def shutdown(self) -> None:
        """Drop all bytes and remove the scratch dir. Idempotent."""
        self._bytes.clear()
        if self._scratch.exists():
            shutil.rmtree(self._scratch, ignore_errors=True)


# --------------------------------------------------------------------
#  Factory
# --------------------------------------------------------------------

_BACKENDS: dict[str, type[Any]] = {
    "fs": FSBlobStore,
    "s3": S3BlobStore,
    "memory": InMemoryBlobStore,
}


_INSTANCES: dict[type[Any], Any] = {}


def get_blob_store(settings: Settings | None = None) -> BlobStore:
    """Build a blob store per ``settings.blob_backend``.

    Backends declare their lifetime via ``is_singleton``: ``False``
    (FS / S3) constructs a fresh stateless wrapper per call; ``True``
    (memory) caches one process-local instance because the bytes live
    in instance state — building a fresh store on every call would
    drop writes silently.

    Callers that need to inject a custom backend in tests should pass
    a constructed instance through dependency injection rather than
    registering globally; ``reset_memory_blob_store_for_tests()``
    drops cached singletons between test cases.
    """
    s = settings or get_settings()
    cls = _BACKENDS.get(s.blob_backend)
    if cls is None:
        raise StorageError(f"unknown blob_backend={s.blob_backend!r}; valid: {sorted(_BACKENDS)}")
    if getattr(cls, "is_singleton", False):
        instance = _INSTANCES.get(cls)
        if instance is None:
            instance = cls(s)
            _INSTANCES[cls] = instance
        return instance
    return cls(s)


def reset_memory_blob_store_for_tests() -> None:
    """Drop cached singleton instances. Call between test cases that
    mutate ``settings.blob_backend`` so each gets a fresh store."""
    cached = _INSTANCES.pop(InMemoryBlobStore, None)
    if cached is not None:
        cached.shutdown()


# --------------------------------------------------------------------
#  TempUploadStore (unchanged — local-only working area for in-flight
#  chunked uploads, finalized into whichever BlobStore is configured).
# --------------------------------------------------------------------


class TempUploadStore:
    """Working area for in-flight chunked uploads, separate from finalized
    blob storage. Each upload gets its own file under
    `workspaces/_uploads/{upload_id}`."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.s = settings or get_settings()
        self.root = Path(self.s.workspace_root) / "_uploads"
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, upload_id: str) -> Path:
        return self.root / upload_id

    def append(self, upload_id: str, offset: int, data: bytes) -> int:
        p = self.path_for(upload_id)
        with p.open("a+b") as fh:
            fh.seek(0, os.SEEK_END)
            current = fh.tell()
            if offset != current:
                raise StorageError(f"Out-of-order chunk: expected offset {current}, got {offset}")
            fh.write(data)
            return fh.tell()

    def size(self, upload_id: str) -> int:
        p = self.path_for(upload_id)
        return p.stat().st_size if p.is_file() else 0

    def hash_and_size(self, upload_id: str) -> tuple[str, int]:
        p = self.path_for(upload_id)
        if not p.is_file():
            return ("", 0)
        h = hashlib.sha256()
        n = 0
        with p.open("rb") as fh:
            while True:
                chunk = fh.read(1 << 20)
                if not chunk:
                    break
                h.update(chunk)
                n += len(chunk)
        return (h.hexdigest(), n)

    def finalize_into(self, upload_id: str, blob_store: BlobStore) -> tuple[str, int]:
        p = self.path_for(upload_id)
        if not p.is_file():
            raise StorageError(f"No upload data for {upload_id}")
        with p.open("rb") as fh:
            sha, total = blob_store.put_stream(fh)
        with contextlib.suppress(FileNotFoundError):
            p.unlink()
        return sha, total

    def discard(self, upload_id: str) -> None:
        p = self.path_for(upload_id)
        with contextlib.suppress(FileNotFoundError):
            p.unlink()


def hash_iter(chunks: Iterable[bytes]) -> tuple[str, int]:
    h = hashlib.sha256()
    n = 0
    for c in chunks:
        h.update(c)
        n += len(c)
    return h.hexdigest(), n


def safe_rmtree(p: Path) -> None:
    if p.exists():
        shutil.rmtree(p, ignore_errors=True)
