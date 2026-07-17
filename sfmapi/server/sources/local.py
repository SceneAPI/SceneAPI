"""LocalPathSource — references a directory the user already owns.

We do NOT copy or symlink (Windows symlinks need admin). We materialize
**by reference**: pycolmap is pointed at the user's path. Drift detection
uses a fingerprint of (path, size, mtime, sample-hash of head/mid/tail
1 MiB) — cheap, stable, catches "user replaced files under us."
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from sfmapi.server.core.errors import StorageError
from sfmapi.server.sources.base import MaterializedImage

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
SAMPLE_BYTES = 1 << 20  # 1 MiB


@dataclass
class LocalPathSource:
    root: Path
    kind: str = "local"
    recursive: bool = True
    extensions: tuple[str, ...] = DEFAULT_EXTENSIONS

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        if not self.root.is_dir():
            raise StorageError(f"Local source root not a directory: {self.root}")

    def _iter_image_paths(self) -> list[Path]:
        it = self.root.rglob("*") if self.recursive else self.root.glob("*")
        out: list[Path] = []
        for p in it:
            if p.is_file() and p.suffix.lower() in self.extensions:
                out.append(p)
        out.sort()
        return out

    def fingerprint(self) -> dict:
        files = []
        for p in self._iter_image_paths():
            st = p.stat()
            files.append(
                {
                    "rel": str(p.relative_to(self.root)).replace("\\", "/"),
                    "size": st.st_size,
                    "mtime_ns": st.st_mtime_ns,
                    "sample": _sample_hash(p, st.st_size),
                }
            )
        return {
            "kind": self.kind,
            "root": str(self.root.resolve()),
            "files": files,
        }

    def materialize(self, into: Path | None = None) -> list[MaterializedImage]:
        # `into` is intentionally ignored — the user owns the bytes.
        out: list[MaterializedImage] = []
        for p in self._iter_image_paths():
            out.append(
                MaterializedImage(
                    name=str(p.relative_to(self.root)).replace("\\", "/"),
                    abs_path=p,
                    content_sha=None,
                )
            )
        return out


def _sample_hash(p: Path, size: int) -> str:
    """Hash the head, middle, and tail 1 MiB. Fixed-cost regardless of
    file size — good enough to detect content mutation cheaply."""
    h = hashlib.sha256()
    if size <= 3 * SAMPLE_BYTES:
        with p.open("rb") as fh:
            h.update(fh.read())
        return h.hexdigest()
    with p.open("rb") as fh:
        head = fh.read(SAMPLE_BYTES)
        fh.seek((size // 2) - SAMPLE_BYTES // 2, os.SEEK_SET)
        mid = fh.read(SAMPLE_BYTES)
        fh.seek(-SAMPLE_BYTES, os.SEEK_END)
        tail = fh.read(SAMPLE_BYTES)
    h.update(head)
    h.update(mid)
    h.update(tail)
    return h.hexdigest()
