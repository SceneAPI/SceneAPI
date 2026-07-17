"""Octree tile generation against synthetic point clouds."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sceneapi.server.schemas.points_binary import (
    HEADER_SIZE,
    Point3DRecord,
    decode_records,
    encode_all,
)
from sceneapi.server.storage import tiles

pytestmark = pytest.mark.unit


def _seal_dir(tmp_path: Path) -> Path:
    snap = tmp_path / "snap"
    snap.mkdir()
    (snap / ".complete").write_text("1")
    return snap


def _write_points(snap: Path, records: list[Point3DRecord]) -> None:
    if not records:
        body = encode_all([], bbox_min=(0.0, 0.0, 0.0), bbox_max=(0.0, 0.0, 0.0))
    else:
        xs = [r.xyz[0] for r in records]
        ys = [r.xyz[1] for r in records]
        zs = [r.xyz[2] for r in records]
        body = encode_all(
            records,
            bbox_min=(min(xs), min(ys), min(zs)),
            bbox_max=(max(xs), max(ys), max(zs)),
        )
    (snap / "points.bin").write_bytes(body)


def _rec(i: int, x: float, y: float, z: float) -> Point3DRecord:
    return Point3DRecord(
        point3d_id=i,
        xyz=(x, y, z),
        rgb=(255, 128, 64),
        track_len=2,
    )


def test_generate_tiles_partitions_octree(tmp_path: Path) -> None:
    snap = _seal_dir(tmp_path)
    # 8 points, one in each of the 8 octree corners of [0,1]^3.
    records = []
    pid = 0
    for ix in (0.1, 0.9):
        for iy in (0.1, 0.9):
            for iz in (0.1, 0.9):
                pid += 1
                records.append(_rec(pid, ix, iy, iz))
    _write_points(snap, records)

    entries = tiles.generate_tiles(snap, max_level=1)
    # Level 0 has one tile with all 8 points.
    lvl0 = [e for e in entries if e.level == 0]
    assert lvl0 == [tiles.TileEntry(0, 0, 0, 0, 8, lvl0[0].byte_size)]
    # Level 1 has 8 tiles, one point each.
    lvl1 = [e for e in entries if e.level == 1]
    assert len(lvl1) == 8
    assert all(e.count == 1 for e in lvl1)


def test_generate_tiles_writes_index_json(tmp_path: Path) -> None:
    snap = _seal_dir(tmp_path)
    _write_points(
        snap,
        [_rec(1, 0.0, 0.0, 0.0), _rec(2, 1.0, 1.0, 1.0)],
    )
    tiles.generate_tiles(snap, max_level=2)
    body = json.loads((snap / "tiles" / "index.json").read_text(encoding="utf-8"))
    assert body["max_level"] == 2
    assert body["tiles"], "index should list at least one tile"
    assert body["bbox_min"] == [0.0, 0.0, 0.0]
    assert body["bbox_max"] == [1.0, 1.0, 1.0]


def test_each_tile_decodes_with_cell_bbox(tmp_path: Path) -> None:
    snap = _seal_dir(tmp_path)
    _write_points(
        snap,
        [_rec(1, 0.1, 0.1, 0.1), _rec(2, 0.9, 0.9, 0.9)],
    )
    tiles.generate_tiles(snap, max_level=1)
    # Two non-empty leaf tiles.
    a = (snap / "tiles" / "1" / "0" / "0" / "0.bin").read_bytes()
    b = (snap / "tiles" / "1" / "1" / "1" / "1.bin").read_bytes()
    recs_a, bmin_a, bmax_a = decode_records(a)
    recs_b, bmin_b, bmax_b = decode_records(b)
    assert len(recs_a) == 1
    assert recs_a[0].point3d_id == 1
    assert len(recs_b) == 1
    assert recs_b[0].point3d_id == 2
    # Cell bbox must contain the point.
    for axis in range(3):
        assert bmin_a[axis] <= recs_a[0].xyz[axis] <= bmax_a[axis] + 1e-6
        assert bmin_b[axis] <= recs_b[0].xyz[axis] <= bmax_b[axis] + 1e-6


def test_empty_points_produces_empty_index(tmp_path: Path) -> None:
    snap = _seal_dir(tmp_path)
    _write_points(snap, [])
    entries = tiles.generate_tiles(snap, max_level=1)
    assert entries == []
    body = json.loads((snap / "tiles" / "index.json").read_text(encoding="utf-8"))
    assert body["tiles"] == []


def test_missing_points_bin_emits_empty_index(tmp_path: Path) -> None:
    snap = _seal_dir(tmp_path)
    entries = tiles.generate_tiles(snap, max_level=1)
    assert entries == []
    assert (snap / "tiles" / "index.json").is_file()


def test_ensure_index_is_lazy(tmp_path: Path) -> None:
    snap = _seal_dir(tmp_path)
    _write_points(snap, [_rec(1, 0.0, 0.0, 0.0)])
    idx = tiles.ensure_index(snap)
    assert idx.is_file()
    mtime = idx.stat().st_mtime_ns
    # Second call should be a no-op (no rewrite).
    idx2 = tiles.ensure_index(snap)
    assert idx == idx2
    assert idx.stat().st_mtime_ns == mtime


def test_tile_size_formula() -> None:
    # 1 record => HEADER_SIZE + 26 bytes
    assert tiles.expected_record_count_for_blob(HEADER_SIZE) == 0
    assert tiles.expected_record_count_for_blob(HEADER_SIZE + 26) == 1
    assert tiles.expected_record_count_for_blob(HEADER_SIZE + 26 * 100) == 100
