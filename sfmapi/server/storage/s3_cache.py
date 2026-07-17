"""Global S3 LRU cache.

Cache layout: `<s3_cache_root>/<bucket>/<sha-of-key>__<etag>` so each
distinct (bucket, key, etag) tuple lives at a unique path. Stale ETags
are evicted; LRU eviction by total bytes when over budget.

Cache is shared across projects/tenants — content addressed by ETag.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from sfmapi.server.core.config import Settings, get_settings


def _key_hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class CachedObject:
    bucket: str
    key: str
    etag: str
    path: Path
    size: int


class S3Cache:
    """Filesystem-backed LRU.

    Atime-driven LRU on the entry's manifest (`.meta.json`) so we don't
    depend on filesystem atime, which is often disabled.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.s = settings or get_settings()
        self.root = Path(self.s.s3_cache_root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, bucket: str, key: str, etag: str) -> Path:
        clean_etag = etag.strip('"').replace("/", "_")[:64]
        return self.root / bucket / f"{_key_hash(key)}__{clean_etag}"

    def lookup(self, bucket: str, key: str, etag: str) -> CachedObject | None:
        p = self.path_for(bucket, key, etag)
        if not p.is_file():
            return None
        self._touch(p)
        return CachedObject(bucket=bucket, key=key, etag=etag, path=p, size=p.stat().st_size)

    def insert(self, *, bucket: str, key: str, etag: str, src_bytes: bytes) -> CachedObject:
        p = self.path_for(bucket, key, etag)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_bytes(src_bytes)
        os.replace(tmp, p)
        meta = p.with_suffix(".meta.json")
        meta.write_text(
            json.dumps(
                {
                    "bucket": bucket,
                    "key": key,
                    "etag": etag,
                    "size": len(src_bytes),
                    "ts": time.time(),
                }
            ),
            encoding="utf-8",
        )
        return CachedObject(bucket=bucket, key=key, etag=etag, path=p, size=len(src_bytes))

    def total_bytes(self) -> int:
        total = 0
        for p in self.root.rglob("*"):
            if p.is_file() and not p.name.endswith(".meta.json") and not p.name.endswith(".tmp"):
                total += p.stat().st_size
        return total

    def evict_to(self, *, max_bytes: int) -> int:
        """Evict least-recently-used entries until under budget. Returns
        bytes freed."""
        files: list[tuple[float, Path, int]] = []
        for p in self.root.rglob("*"):
            if p.is_file() and not p.name.endswith(".meta.json") and not p.name.endswith(".tmp"):
                meta = p.with_suffix(".meta.json")
                ts = meta.stat().st_mtime if meta.is_file() else p.stat().st_mtime
                files.append((ts, p, p.stat().st_size))
        files.sort(key=lambda t: t[0])  # oldest first
        total = sum(s for _, _, s in files)
        freed = 0
        for _ts, p, sz in files:
            if total - freed <= max_bytes:
                break
            try:
                p.unlink(missing_ok=True)
                p.with_suffix(".meta.json").unlink(missing_ok=True)
                freed += sz
            except OSError:
                continue
        return freed

    def _touch(self, p: Path) -> None:
        meta = p.with_suffix(".meta.json")
        now = time.time()
        with contextlib.suppress(OSError):
            os.utime(meta, (now, now))

    def clear(self) -> None:
        if self.root.exists():
            shutil.rmtree(self.root)
            self.root.mkdir(parents=True, exist_ok=True)
