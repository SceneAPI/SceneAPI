"""Locks the COLMAP scene-database core contract (app.core.colmap_db).

The contract mirrors the extended colmap_mod schema. These tests pin
the version, the fork-extension surface, and the pair_id encoding so
drift from the reference fork is caught here rather than at runtime.
"""

from __future__ import annotations

import pytest

from app.core import colmap_db as db


def test_database_version_number_matches_colmap_mod() -> None:
    # colmap_mod: COLMAP 3.14.0, schema revision 2 -> 3*1e6+14*1e4+0+2.
    assert db.DATABASE_VERSION_NUMBER == 3_140_002
    assert db.DATABASE_SCHEMA_REVISION == 2


def test_make_version_number_rejects_overflowing_components() -> None:
    with pytest.raises(ValueError):
        db.make_database_version_number(3, 100, 0, 0)


def test_pair_id_encoding_roundtrips_and_orders() -> None:
    # Smaller id lands in the high digits; encode/decode is exact.
    assert db.image_pair_to_pair_id(2, 5) == db.image_pair_to_pair_id(5, 2)
    for a, b in [(0, 1), (1, 2), (7, 99), (123, 456), (1, db.MAX_NUM_IMAGES - 1)]:
        pid = db.image_pair_to_pair_id(a, b)
        lo, hi = db.pair_id_to_image_pair(pid)
        assert (lo, hi) == (min(a, b), max(a, b))


def test_pair_id_rejects_out_of_range_ids() -> None:
    with pytest.raises(ValueError):
        db.image_pair_to_pair_id(-1, 2)
    with pytest.raises(ValueError):
        db.image_pair_to_pair_id(db.MAX_NUM_IMAGES, 2)


def test_extension_tables_are_exactly_the_fork_additions() -> None:
    assert db.EXTENSION_TABLES == frozenset(
        {"videos", "video_frames", "image_qualities", "markers", "marker_projections"}
    )


def test_extension_columns_are_4d_time_and_descriptor_type() -> None:
    # Two extension columns on otherwise-upstream tables: the 4D
    # per-image capture tag, and the descriptor extractor type.
    assert db.EXTENSION_COLUMNS == frozenset(
        {"images.time_id", "descriptors.type"}
    )


def test_images_time_id_is_the_canonical_4d_extension() -> None:
    images = db.COLMAP_DB_TABLES_BY_NAME["images"]
    assert [c.name for c in images.columns] == [
        "image_id", "name", "camera_id", "time_id",
    ]
    time_id = images.column("time_id")
    assert time_id is not None
    # 4D tag is an extension over vanilla upstream, not part of the
    # portable core; images table itself stays an upstream table.
    assert time_id.extension
    assert not images.extension


def test_video_frames_also_carries_time_id() -> None:
    # video_frames.time_id is the video-source echo of the per-image tag.
    vf = db.COLMAP_DB_TABLES_BY_NAME["video_frames"]
    assert vf.column("time_id") is not None
    assert vf.extension


def test_upstream_and_extension_partition_is_complete() -> None:
    all_tables = {t.name for t in db.COLMAP_DB_TABLES}
    assert db.UPSTREAM_TABLES | db.EXTENSION_TABLES == all_tables
    assert db.UPSTREAM_TABLES & db.EXTENSION_TABLES == frozenset()


def test_core_contract_does_not_import_colmap_plugin() -> None:
    # The contract is a data standard, not a dependency: importing it must
    # not pull in any sfmapi_colmap* plugin package. (The repo-wide guard
    # in test_repo_boundary_guards also enforces this statically.)
    import sys

    before = set(sys.modules)
    import importlib

    importlib.reload(db)
    leaked = {
        m for m in (set(sys.modules) - before) if m.startswith("sfmapi_")
    }
    assert not leaked, f"contract import leaked plugin modules: {leaked}"
