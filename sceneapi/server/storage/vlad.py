"""VLAD index storage + NumPy cosine query.

The on-disk format is a single ``vlad.npz`` per dataset:

  - ``vectors``: float32 ``(N, D)`` matrix, **L2-normalized rows**
  - ``image_ids``: 1-D string array of length ``N`` (sfmapi image_ids,
    not pycolmap's internal image_id integers — workers map across)
  - ``manifest``: 0-D string array holding a JSON manifest with keys
    ``manifest_hash`` (the dataset's manifest_hash at build time) and
    ``dim`` (== ``D``)

The web process reads this file with NumPy alone — no pycolmap needed
for queries. Building the index requires pycolmap (it walks the SIFT
database) and lives in ``sceneapi/server/workers/tasks/vlad_index.py``.

Cosine similarity is computed as ``vectors @ q`` because rows are
pre-normalized; sorting in descending dot is the same as ascending
distance ``1 - dot``. We expose distance ``d = max(0, 1 - dot)`` so
clients see a consistent monotone-in-dissimilarity scalar across
``dhash`` (Hamming integer) and ``vlad`` (float in ``[0, 2]``).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # annotation-only — see lazy imports below
    import numpy as np

# NumPy is imported lazily inside the functions that use it: this
# module sits on the web-process import path (api/v1/similarity ->
# services/similarity_service -> here) and a module-level import would
# charge every web process the numpy startup cost. Guarded by the
# import-guard test in tests/unit/test_app_starts.py.

VLAD_FILE = "vlad.npz"


@dataclass(frozen=True)
class VladIndex:
    image_ids: list[str]
    vectors: np.ndarray  # (N, D), float32, L2-normalized
    manifest_hash: str
    dim: int

    def index_of(self, image_id: str) -> int:
        try:
            return self.image_ids.index(image_id)
        except ValueError as e:
            raise KeyError(image_id) from e


@dataclass(frozen=True)
class VladNeighbor:
    image_id: str
    distance: float

    def as_dict(self) -> dict:
        return {"image_id": self.image_id, "distance": self.distance}


def index_path(dataset_dir: Path) -> Path:
    return dataset_dir / "similarity" / VLAD_FILE


def write_index(
    dataset_dir: Path,
    *,
    image_ids: list[str],
    vectors: np.ndarray,
    manifest_hash: str,
) -> Path:
    """Persist a VLAD index. Vectors are L2-normalized in-place."""
    import numpy as np

    if vectors.ndim != 2 or vectors.shape[0] != len(image_ids):
        raise ValueError(f"vectors shape {vectors.shape} doesn't match {len(image_ids)} ids")
    vectors = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    vectors = vectors / norms
    p = index_path(dataset_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    manifest = json.dumps(
        {"manifest_hash": manifest_hash, "dim": int(vectors.shape[1])}, sort_keys=True
    )
    # NumPy auto-appends ``.npz`` to paths that don't end in it, so
    # write to a fully-qualified ``.npz`` temp path and rename.
    tmp = p.with_name(p.name + ".tmp.npz")
    np.savez_compressed(
        str(tmp),
        vectors=vectors,
        image_ids=np.array(image_ids, dtype=np.str_),
        manifest=np.array(manifest, dtype=np.str_),
    )
    os.replace(tmp, p)
    return p


def read_index(dataset_dir: Path) -> VladIndex | None:
    import numpy as np

    p = index_path(dataset_dir)
    if not p.is_file():
        return None
    with np.load(p, allow_pickle=False) as data:
        vectors = np.asarray(data["vectors"], dtype=np.float32)
        image_ids = [str(s) for s in data["image_ids"].tolist()]
        manifest = json.loads(str(data["manifest"]))
    return VladIndex(
        image_ids=image_ids,
        vectors=vectors,
        manifest_hash=str(manifest.get("manifest_hash", "")),
        dim=int(manifest.get("dim", vectors.shape[1] if vectors.ndim == 2 else 0)),
    )


def k_nearest(
    index: VladIndex, *, image_id: str, k: int = 5, include_self: bool = False
) -> list[VladNeighbor]:
    """Top-K nearest by cosine distance. Raises KeyError if image_id
    not in the index."""
    import numpy as np

    qi = index.index_of(image_id)
    q = index.vectors[qi]
    sims = index.vectors @ q  # (N,)
    distances = np.maximum(0.0, 1.0 - sims)
    order = np.argsort(distances, kind="stable")
    out: list[VladNeighbor] = []
    for idx in order:
        if not include_self and idx == qi:
            continue
        out.append(VladNeighbor(image_id=index.image_ids[int(idx)], distance=float(distances[idx])))
        if len(out) >= k:
            break
    return out


__all__ = [
    "VLAD_FILE",
    "VladIndex",
    "VladNeighbor",
    "index_path",
    "k_nearest",
    "read_index",
    "write_index",
]
