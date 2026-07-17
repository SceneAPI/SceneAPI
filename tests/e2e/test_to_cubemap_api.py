"""POST /v1/reconstructions/{rid}:toCubemap endpoint contract."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e


async def test_to_cubemap_returns_501_without_capability(client) -> None:
    """Without pycolmap the backend doesn't advertise
    projection.cubemap_rig, so POST returns 501."""
    resp = await client.post("/v1/reconstructions/01HGHOST00000000000000000A:toCubemap")
    assert resp.status_code == 501
    assert resp.json()["capability"] == "projection.cubemap_rig"
