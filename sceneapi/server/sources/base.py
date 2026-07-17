"""Image source abstraction.

Three concerns are intentionally separated:

  - `ImageSource`: a logical, persisted reference to where bytes live
    (uploaded blobs, a local directory, or an S3 prefix). Persisted via
    the `image_source` table.

  - `Materialization`: a per-job realization of an image source as a
    real local directory pycolmap can read. Owned by the worker; not
    persisted — built on demand inside `sceneapi/server/workers/...`.

  - `Fingerprint`: a deterministic JSON snapshot of the source's
    contents (hashes, mtimes, sizes) used as cache-invalidation
    evidence. For uploads this collapses to the blob shas; for local
    paths we add (path, size, mtime, sample-hash) to detect mutation
    under us without reading the whole file.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class MaterializedImage:
    name: str
    abs_path: Path
    content_sha: str | None = None  # known for upload+s3, optional for local


class ImageSourceImpl(Protocol):
    kind: str

    def fingerprint(self) -> dict: ...

    def materialize(self, into: Path) -> list[MaterializedImage]:
        """Realize images at `into` (or referenced from `into`).

        For sources sfmapi owns (upload, s3-cache), `into` is the
        materialization target. For local sources, `into` may be ignored
        and the source's own path is returned by reference.
        """
        ...
