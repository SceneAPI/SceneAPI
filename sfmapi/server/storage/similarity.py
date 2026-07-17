"""Image similarity index + perceptual hash.

Two strategies are reserved by the spec; one is implemented today:

  - **dhash** (default): a 64-bit perceptual difference hash. Works
    on raw image bytes and is independent of any SfM engine, but it
    needs the optional Pillow image-processing extra to decode pixels.
    Tuned for "near-duplicate" detection and quick similarity queries.

  - **vlad**: SfM-grade VLAD descriptors. Requires pycolmap (and an
    extracted feature database) and is built by a worker. Stub here
    raises `NotImplementedError`; the API surface returns 503 when
    invoked without pycolmap.

Index storage
-------------
Indexes are persisted under the dataset workspace at
`<dataset>/similarity/{strategy}.json`. Each file stores the
manifest_hash it was built against so callers can detect when the
underlying images have changed and the index is stale.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from sfmapi.server.core.errors import CapabilityUnavailableError

DHASH_SIZE = 8  # produces an 8x8 = 64-bit hash


@dataclass(frozen=True)
class SimilarityNeighbor:
    image_id: str
    distance: int

    def as_dict(self) -> dict[str, int | str]:
        return {"image_id": self.image_id, "distance": self.distance}


@dataclass(frozen=True)
class SimilarityIndex:
    strategy: str
    manifest_hash: str
    hashes: dict[str, str]  # image_id -> hex string

    def as_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "manifest_hash": self.manifest_hash,
            "count": len(self.hashes),
            "hashes": self.hashes,
        }


# ---- dHash -----------------------------------------------------------------


def dhash_bytes(data: bytes | BinaryIO) -> int:
    """Return a 64-bit dHash of an image.

    Algorithm: resize to (DHASH_SIZE+1) x DHASH_SIZE grayscale, then
    for each row compare adjacent pixels — bit = (left > right).
    """
    if isinstance(data, (bytes, bytearray, memoryview)):
        fh: BinaryIO = io.BytesIO(bytes(data))
    else:
        fh = data
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:
        raise CapabilityUnavailableError(
            capability="similarity.dhash",
            reason="dhash similarity requires the optional Pillow dependency",
        ) from exc

    with Image.open(fh) as opened:
        im = ImageOps.exif_transpose(opened)
        im = im.convert("L").resize((DHASH_SIZE + 1, DHASH_SIZE), Image.Resampling.LANCZOS)
        # `tobytes()` returns row-major pixel data — same layout `getdata()`
        # produced and stable across Pillow versions.
        pixels = list(im.tobytes())
    h = 0
    bit = 0
    for row in range(DHASH_SIZE):
        offset = row * (DHASH_SIZE + 1)
        for col in range(DHASH_SIZE):
            left = pixels[offset + col]
            right = pixels[offset + col + 1]
            if left > right:
                h |= 1 << bit
            bit += 1
    return h


def dhash_hex(data: bytes | BinaryIO) -> str:
    return f"{dhash_bytes(data):016x}"


def hamming(a: str, b: str) -> int:
    """Hamming distance between two hex-encoded 64-bit hashes."""
    return bin(int(a, 16) ^ int(b, 16)).count("1")


# ---- index storage ---------------------------------------------------------


def index_root(dataset_dir: Path) -> Path:
    return dataset_dir / "similarity"


def index_path(dataset_dir: Path, strategy: str) -> Path:
    return index_root(dataset_dir) / f"{strategy}.json"


def write_index(dataset_dir: Path, index: SimilarityIndex) -> Path:
    p = index_path(dataset_dir, index.strategy)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(index.as_dict(), indent=2, sort_keys=True), encoding="utf-8")
    import os

    os.replace(tmp, p)
    return p


def read_index(dataset_dir: Path, strategy: str) -> SimilarityIndex | None:
    p = index_path(dataset_dir, strategy)
    if not p.is_file():
        return None
    body = json.loads(p.read_text(encoding="utf-8"))
    return SimilarityIndex(
        strategy=body["strategy"],
        manifest_hash=body.get("manifest_hash", ""),
        hashes=body.get("hashes", {}),
    )


# ---- query -----------------------------------------------------------------


def k_nearest(
    index: SimilarityIndex,
    *,
    image_id: str,
    k: int = 5,
    include_self: bool = False,
) -> list[SimilarityNeighbor]:
    """Return up to `k` nearest neighbors of `image_id` by Hamming
    distance. Raises `KeyError` if `image_id` is not in the index."""
    if image_id not in index.hashes:
        raise KeyError(image_id)
    query = index.hashes[image_id]
    out: list[SimilarityNeighbor] = []
    for other_id, other_hash in index.hashes.items():
        if not include_self and other_id == image_id:
            continue
        out.append(SimilarityNeighbor(image_id=other_id, distance=hamming(query, other_hash)))
    out.sort(key=lambda n: (n.distance, n.image_id))
    return out[:k]
