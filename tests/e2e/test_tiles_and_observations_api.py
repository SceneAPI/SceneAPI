"""Tile + observations + visibility API endpoints."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.hashing import canonical_json, content_address
from app.core.ids import new_id
from app.core.paths import Paths
from app.db.models import (
    Dataset,
    ImageSource,
    Project,
    Reconstruction,
    RuntimeVersion,
)
from app.schemas.points_binary import Point3DRecord, encode_all
from app.storage import observations as obs
from app.storage.snapshots import SnapshotStore

pytestmark = pytest.mark.e2e


async def _seed_recon_with_snapshot(session) -> tuple[str, str, int, Path]:
    """Create a Project + Dataset + Reconstruction + a sealed snapshot
    with a small synthetic `points.bin`. Returns
    `(project_id, recon_id, seq, snapshot_dir)`.
    """
    rv = RuntimeVersion(
        rv_id=new_id(),
        runtime_version_id="test-rv",
        seed="0",
    )
    p = Project(tenant_id="default", name="tiles-p")
    src = ImageSource(tenant_id="default", kind="upload", fingerprint_json={})
    session.add_all([rv, p, src])
    await session.flush()
    d = Dataset(
        tenant_id="default",
        project_id=p.project_id,
        source_id=src.source_id,
        name="ds",
    )
    session.add(d)
    await session.flush()
    r = Reconstruction(
        tenant_id="default",
        project_id=p.project_id,
        dataset_id=d.dataset_id,
        dataset_snapshot_hash=content_address(canonical_json({"images": []})),
        spec_json={"kind": "incremental"},
        rv_id=rv.rv_id,
        status="succeeded",
    )
    session.add(r)
    await session.commit()

    paths = Paths()
    rec_root = paths.reconstruction_root("default", p.project_id, r.recon_id)
    rec_root.mkdir(parents=True, exist_ok=True)
    src_dir = rec_root / "live"
    src_dir.mkdir(exist_ok=True)
    # Write points.bin with 4 well-distributed points.
    records = [
        Point3DRecord(point3d_id=i, xyz=xyz, rgb=(255, 128, 64), track_len=3)
        for i, xyz in enumerate(
            [(0.1, 0.1, 0.1), (0.9, 0.1, 0.1), (0.1, 0.9, 0.9), (0.9, 0.9, 0.9)],
            start=1,
        )
    ]
    body = encode_all(records, bbox_min=(0.0, 0.0, 0.0), bbox_max=(1.0, 1.0, 1.0))
    (src_dir / "points.bin").write_bytes(body)
    (src_dir / "summary.json").write_text(json.dumps({"models": []}))

    store = SnapshotStore(rec_root)
    sealed = store.seal(seq=1, source_dir=src_dir, summary={"phase": "test"})
    return p.project_id, r.recon_id, 1, sealed


async def test_tile_index_lists_tiles(client, session) -> None:
    _, rid, seq, _ = await _seed_recon_with_snapshot(session)
    resp = await client.get(
        f"/v1/reconstructions/{rid}/snapshots/{seq}/tiles/index.json?max_level=1"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["max_level"] == 1
    # Level 0 = 1 tile with 4 points; level 1 = 4 leaf tiles.
    levels = sorted({t["level"] for t in body["tiles"]})
    assert levels == [0, 1]
    counts = {lv: sum(t["count"] for t in body["tiles"] if t["level"] == lv) for lv in levels}
    assert counts[0] == 4
    assert counts[1] == 4
    assert resp.headers.get("etag")


async def test_tile_bytes_returns_points_v1(client, session) -> None:
    _, rid, seq, _ = await _seed_recon_with_snapshot(session)
    # Trigger generation by hitting the index first.
    await client.get(f"/v1/reconstructions/{rid}/snapshots/{seq}/tiles/index.json")
    resp = await client.get(f"/v1/reconstructions/{rid}/snapshots/{seq}/tiles/0/0/0/0.bin")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/x-sfm-points-v1"
    assert resp.content[:8] == b"SFMP3D\x00\x00"


async def test_missing_tile_returns_404(client, session) -> None:
    _, rid, seq, _ = await _seed_recon_with_snapshot(session)
    await client.get(f"/v1/reconstructions/{rid}/snapshots/{seq}/tiles/index.json")
    # All 8 cells at level 1 are NOT populated (only 4 corners are).
    # An unoccupied cell returns 404.
    resp = await client.get(f"/v1/reconstructions/{rid}/snapshots/{seq}/tiles/1/0/0/1.bin")
    assert resp.status_code == 404


async def test_observations_404_when_sidecar_missing(client, session) -> None:
    _, rid, seq, _ = await _seed_recon_with_snapshot(session)
    resp = await client.get(f"/v1/reconstructions/{rid}/snapshots/{seq}/images/img-1/observations")
    assert resp.status_code == 404
    body = resp.json()
    assert "sidecar" in (body.get("detail", "") + body.get("title", "")).lower()


async def test_observations_returns_payload_when_present(client, session) -> None:
    _, rid, seq, snap_dir = await _seed_recon_with_snapshot(session)
    obs.write_observations_by_image(
        snap_dir,
        by_image={
            "img-1": [
                obs.ImageObservationRow(point3d_id=1, x=10.0, y=20.0, kp_idx=0, error=0.5),
                obs.ImageObservationRow(point3d_id=2, x=11.0, y=22.0, kp_idx=1),
            ],
        },
    )
    resp = await client.get(f"/v1/reconstructions/{rid}/snapshots/{seq}/images/img-1/observations")
    assert resp.status_code == 200
    body = resp.json()
    assert body["image_id"] == "img-1"
    assert body["count"] == 2
    assert body["observations"][0]["point3d_id"] == 1


async def test_visibility_returns_payload_when_present(client, session) -> None:
    _, rid, seq, snap_dir = await _seed_recon_with_snapshot(session)
    obs.write_observations_by_point(
        snap_dir,
        by_point={
            "42": [
                obs.PointObservationRow(image_id=10, x=5.0, y=5.0, kp_idx=0),
                obs.PointObservationRow(image_id=11, x=4.0, y=6.0, kp_idx=2),
            ],
        },
    )
    resp = await client.get(f"/v1/reconstructions/{rid}/snapshots/{seq}/points/42/visibility")
    assert resp.status_code == 200
    body = resp.json()
    assert body["point3d_id"] == "42"
    assert body["count"] == 2


async def test_snapshot_list_includes_tile_link(client, session) -> None:
    _, rid, _, _ = await _seed_recon_with_snapshot(session)
    resp = await client.get(f"/v1/reconstructions/{rid}/snapshots")
    assert resp.status_code == 200
    body = resp.json()
    latest = body["_links"]["latest"]
    assert latest is not None
    assert latest["tiles_index"]["href"].endswith("/tiles/index.json")
    assert latest["points"]["href"].endswith("/points.bin")
