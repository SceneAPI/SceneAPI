"""GET /v1/capabilities."""

from __future__ import annotations

import pytest

from sceneapi.server.core.capabilities import CORE_CAPABILITIES

pytestmark = pytest.mark.e2e


async def test_capabilities_returns_backend_and_features(client) -> None:
    resp = await client.get("/v1/capabilities")
    assert resp.status_code == 200
    body = resp.json()
    assert "backend" in body
    assert "features" in body
    assert body["backend"]["name"]
    for name in CORE_CAPABILITIES:
        assert body["features"].get(name) is True


async def test_capabilities_features_have_canonical_names(client) -> None:
    """Every reported flag is either CORE or OPTIONAL, never an
    invented name. Backends MAY add extra keys but we don't here."""
    from sceneapi.server.core.capabilities import ALL_KNOWN

    resp = await client.get("/v1/capabilities")
    body = resp.json()
    extra = set(body["features"]) - ALL_KNOWN
    assert extra == set(), f"unexpected feature keys: {sorted(extra)}"
