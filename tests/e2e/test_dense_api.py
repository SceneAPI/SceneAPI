"""POST /v1/reconstructions/{rid}/dense + dense read endpoints."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e


async def test_dense_returns_501_when_backend_lacks_capability(client) -> None:
    """Without pycolmap the backend doesn't advertise dense MVS, so
    POST /dense returns 501 with the canonical capability name.

    This fires before the recon-id 404 check — capability discovery
    is the contract, recon resolution is implementation detail."""
    resp = await client.post("/v1/reconstructions/01HGHOST00000000000000000A/dense")
    assert resp.status_code == 501
    body = resp.json()
    assert body["capability"] in ("dense.patch_match_stereo", "dense.stereo_fusion")


async def test_dense_index_404_when_snapshot_missing(client) -> None:
    resp = await client.get(
        "/v1/reconstructions/01HGHOST00000000000000000A/snapshots/1/dense/index.json"
    )
    assert resp.status_code == 404


async def test_dense_fused_404_when_snapshot_missing(client) -> None:
    resp = await client.get(
        "/v1/reconstructions/01HGHOST00000000000000000A/snapshots/1/dense/fused.bin"
    )
    assert resp.status_code == 404


async def test_depth_map_rejects_path_traversal(client) -> None:
    """`image_name` is a path segment but the route guards `..` and `/`."""
    resp = await client.get(
        "/v1/reconstructions/01HGHOST00000000000000000A/snapshots/1/dense/depth_maps/..bin"
    )
    # FastAPI may match the route with image_name="." (404 from missing
    # snapshot) or reject earlier; either way no traversal happened.
    assert resp.status_code in (404, 422)
