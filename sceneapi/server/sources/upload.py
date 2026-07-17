"""UploadSource — bytes live in the content-addressed blob store.

Materialization links/copies blobs into a working dir under their image
names so pycolmap sees a regular dir of `IMG_001.jpg, IMG_002.jpg, ...`
"""

from __future__ import annotations

import contextlib
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from sceneapi.server.core.errors import StorageError
from sceneapi.server.sources.base import MaterializedImage
from sceneapi.server.storage.blobs import BlobStore, get_blob_store


@dataclass
class UploadEntry:
    name: str
    blob_sha: str


@dataclass
class UploadSource:
    kind: str = "upload"
    entries: list[UploadEntry] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.entries is None:
            self.entries = []

    def fingerprint(self) -> dict:
        # Sorted by (name, sha) — deterministic.
        sorted_entries = sorted((e.name, e.blob_sha) for e in self.entries)
        return {"kind": self.kind, "entries": sorted_entries}

    def materialize(
        self, into: Path, blob_store: BlobStore | None = None
    ) -> list[MaterializedImage]:
        bs = blob_store or get_blob_store()
        into.mkdir(parents=True, exist_ok=True)
        out: list[MaterializedImage] = []
        for entry in self.entries:
            try:
                src = bs.local_path(entry.blob_sha)
            except StorageError:
                raise StorageError(f"Blob missing: {entry.blob_sha}") from None
            dst = into / entry.name
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                dst.unlink()
            try:
                # Hardlink first; falls back to copy across volumes / on Windows.
                os.link(src, dst)
            except OSError:
                with contextlib.suppress(OSError):
                    shutil.copy2(src, dst)
            out.append(MaterializedImage(name=entry.name, abs_path=dst, content_sha=entry.blob_sha))
        return out
