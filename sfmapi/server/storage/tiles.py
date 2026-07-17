"""Octree tile generation for sealed-snapshot point clouds.

Tiles are lazy: the first request for a given snapshot triggers a one-shot
generation pass that writes every non-empty tile + an `index.json` under
`<snapshot>/tiles/`. Subsequent requests serve the cached file directly.

Address scheme
--------------
Each tile is identified by `(level, x, y, z)`:

  - `level=0` is a single tile covering the full bbox.
  - At level L the bbox is partitioned into a 2^L cube; `x, y, z` are
    integer coordinates in `[0, 2^L)`.
  - A point belongs to the tile whose half-open cell contains it.

Wire format
-----------
Each tile file is `application/x-sfm-points-v1` (the same binary format as
`points.bin`). The header's `bbox_min`/`bbox_max` are the tile cell, not
the dataset bbox, so a client can render a tile without needing the
parent index.

Generation policy
-----------------
We bound the worst-case file count by capping `max_level` at 4 (8^4 = 4096
cells). For very large clouds (>1M points) callers should request a deeper
level; for typical small reconstructions level 1-2 is enough. Empty cells
are simply omitted from the index — clients filter `index.json` for the
tiles they want.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from sfmapi.server.schemas.points_binary import (
    HEADER_SIZE,
    RECORD_SIZE,
    Point3DRecord,
    decode_records,
    encode_all,
)

DEFAULT_MAX_LEVEL = 4
MAX_LEVEL_HARD_CAP = 6  # 8^6 = 262_144 cells; bigger than that is silly


@dataclass(frozen=True)
class TileAddress:
    level: int
    x: int
    y: int
    z: int


@dataclass(frozen=True)
class TileEntry:
    level: int
    x: int
    y: int
    z: int
    count: int
    byte_size: int

    def as_dict(self) -> dict:
        return {
            "level": self.level,
            "x": self.x,
            "y": self.y,
            "z": self.z,
            "count": self.count,
            "byte_size": self.byte_size,
        }


def tiles_root(snapshot_dir: Path) -> Path:
    return snapshot_dir / "tiles"


def index_path(snapshot_dir: Path) -> Path:
    return tiles_root(snapshot_dir) / "index.json"


def tile_path(snapshot_dir: Path, addr: TileAddress) -> Path:
    return tiles_root(snapshot_dir) / str(addr.level) / str(addr.x) / str(addr.y) / f"{addr.z}.bin"


def _validate_level(level: int) -> int:
    if level < 0 or level > MAX_LEVEL_HARD_CAP:
        raise ValueError(f"level {level} out of range [0, {MAX_LEVEL_HARD_CAP}]")
    return level


def _cell_for(
    point: tuple[float, float, float],
    bbox_min: tuple[float, float, float],
    bbox_max: tuple[float, float, float],
    level: int,
) -> tuple[int, int, int]:
    cells = max(1, 2**level)
    out: list[int] = []
    for dim in range(3):
        lo, hi = bbox_min[dim], bbox_max[dim]
        span = hi - lo
        if span <= 0:
            out.append(0)
            continue
        t = (point[dim] - lo) / span
        # Half-open partition: clamp to [0, cells-1].
        idx = min(cells - 1, max(0, int(t * cells)))
        out.append(idx)
    return out[0], out[1], out[2]


def _cell_bbox(
    bbox_min: tuple[float, float, float],
    bbox_max: tuple[float, float, float],
    level: int,
    x: int,
    y: int,
    z: int,
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    cells = max(1, 2**level)
    out_min: list[float] = []
    out_max: list[float] = []
    for dim, idx in enumerate((x, y, z)):
        lo, hi = bbox_min[dim], bbox_max[dim]
        span = (hi - lo) / cells
        out_min.append(lo + span * idx)
        out_max.append(lo + span * (idx + 1))
    return (
        (out_min[0], out_min[1], out_min[2]),
        (out_max[0], out_max[1], out_max[2]),
    )


def generate_tiles(snapshot_dir: Path, *, max_level: int = DEFAULT_MAX_LEVEL) -> list[TileEntry]:
    """Read `<snapshot>/points.bin`, partition into octree tiles up to
    `max_level`, write every non-empty tile + `index.json`. Returns the
    sorted list of tile entries written. If the points file is missing
    or empty, writes an empty index and returns `[]`.
    """
    _validate_level(max_level)
    points_bin = snapshot_dir / "points.bin"
    out_root = tiles_root(snapshot_dir)
    out_root.mkdir(parents=True, exist_ok=True)
    idx = index_path(snapshot_dir)

    if not points_bin.is_file():
        idx.write_text(
            json.dumps(
                {"bbox_min": None, "bbox_max": None, "max_level": max_level, "tiles": []},
                indent=2,
            ),
            encoding="utf-8",
        )
        return []

    raw = points_bin.read_bytes()
    if len(raw) <= HEADER_SIZE:
        idx.write_text(
            json.dumps(
                {"bbox_min": None, "bbox_max": None, "max_level": max_level, "tiles": []},
                indent=2,
            ),
            encoding="utf-8",
        )
        return []
    records, bbox_min, bbox_max = decode_records(raw)

    entries: list[TileEntry] = []
    for level in range(max_level + 1):
        buckets: dict[tuple[int, int, int], list[Point3DRecord]] = defaultdict(list)
        for rec in records:
            cell = _cell_for(rec.xyz, bbox_min, bbox_max, level)
            buckets[cell].append(rec)
        for (x, y, z), recs in buckets.items():
            cell_min, cell_max = _cell_bbox(bbox_min, bbox_max, level, x, y, z)
            blob = encode_all(recs, bbox_min=cell_min, bbox_max=cell_max)
            target = tile_path(snapshot_dir, TileAddress(level, x, y, z))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(blob)
            entries.append(TileEntry(level, x, y, z, count=len(recs), byte_size=len(blob)))

    entries.sort(key=lambda e: (e.level, e.x, e.y, e.z))
    idx.write_text(
        json.dumps(
            {
                "bbox_min": list(bbox_min),
                "bbox_max": list(bbox_max),
                "max_level": max_level,
                "tile_count": len(entries),
                "point_count": sum(e.count for e in entries) // (max_level + 1)
                if max_level >= 0
                else 0,
                "tiles": [e.as_dict() for e in entries],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return entries


def ensure_index(snapshot_dir: Path, *, max_level: int = DEFAULT_MAX_LEVEL) -> Path:
    idx = index_path(snapshot_dir)
    if not idx.is_file():
        generate_tiles(snapshot_dir, max_level=max_level)
    return idx


def expected_record_count_for_blob(byte_size: int) -> int:
    """Inverse of the wire format: how many records does a tile of
    this size contain? Useful for clients streaming tiles via Range
    requests."""
    if byte_size < HEADER_SIZE:
        return 0
    return (byte_size - HEADER_SIZE) // RECORD_SIZE
