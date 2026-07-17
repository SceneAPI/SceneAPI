"""S3Source — Phase 5 GA.

Lazily downloads bytes for each image in a bucket+prefix to a global
LRU cache shared across projects+tenants (keyed by `(bucket, key,
etag)`). Materialization returns paths into the cache; pycolmap reads
them as if local.

Authentication: standard boto3 chain (env vars, profile, IAM role).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sfmapi.server.core.errors import StorageError
from sfmapi.server.sources.base import MaterializedImage
from sfmapi.server.storage.s3_cache import S3Cache

DEFAULT_EXTENSIONS = (
    ".jpg",
    ".jpeg",
    ".png",
    ".tif",
    ".tiff",
    ".bmp",
    ".webp",
    ".heic",
    ".heif",
)


@dataclass
class S3Source:
    bucket: str
    prefix: str = ""
    kind: str = "s3"
    extensions: tuple[str, ...] = DEFAULT_EXTENSIONS
    region_name: str | None = None
    endpoint_url: str | None = None
    _client: Any | None = field(default=None, repr=False, compare=False)

    def _s3(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import boto3  # type: ignore[import-not-found]
        except ImportError as e:
            raise StorageError(f"boto3 not installed: {e}") from e
        kwargs: dict[str, Any] = {}
        if self.region_name:
            kwargs["region_name"] = self.region_name
        if self.endpoint_url:
            kwargs["endpoint_url"] = self.endpoint_url
        self._client = boto3.client("s3", **kwargs)
        return self._client

    def _list_objects(self) -> list[dict]:
        s3 = self._s3()
        out: list[dict] = []
        token: str | None = None
        while True:
            kwargs = {"Bucket": self.bucket, "Prefix": self.prefix}
            if token:
                kwargs["ContinuationToken"] = token
            resp = s3.list_objects_v2(**kwargs)
            for obj in resp.get("Contents", []) or []:
                key = obj["Key"]
                ext = "." + key.rsplit(".", 1)[-1].lower() if "." in key else ""
                if ext in self.extensions:
                    out.append({"Key": key, "ETag": obj["ETag"], "Size": obj["Size"]})
            token = resp.get("NextContinuationToken")
            if not token:
                break
        out.sort(key=lambda o: o["Key"])
        return out

    def fingerprint(self) -> dict:
        objs = self._list_objects()
        return {
            "kind": self.kind,
            "bucket": self.bucket,
            "prefix": self.prefix,
            "objects": [
                {"key": o["Key"], "etag": o["ETag"].strip('"'), "size": o["Size"]} for o in objs
            ],
        }

    def materialize(self, into: Path | None = None) -> list[MaterializedImage]:
        cache = S3Cache()
        s3 = self._s3()
        out: list[MaterializedImage] = []
        for obj in self._list_objects():
            key = obj["Key"]
            etag = obj["ETag"].strip('"')
            cached = cache.lookup(self.bucket, key, etag)
            if cached is None:
                resp = s3.get_object(Bucket=self.bucket, Key=key)
                body = resp["Body"].read()
                cached = cache.insert(bucket=self.bucket, key=key, etag=etag, src_bytes=body)
            name = key[len(self.prefix) :].lstrip("/") if self.prefix else key
            out.append(
                MaterializedImage(
                    name=name or key,
                    abs_path=cached.path,
                    content_sha=None,
                )
            )
        return out
