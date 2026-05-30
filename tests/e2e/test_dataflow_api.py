"""POST /v1/pipelines:validate -- typed-dataflow pre-flight type-checking."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.anyio


async def _client() -> AsyncClient:
    from app.main import create_app

    return AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://t")


async def test_valid_chain_passes() -> None:
    async with await _client() as client:
        r = await client.post("/v1/pipelines:validate", json={
            "steps": ["colmap.feature_extractor", "colmap.exhaustive_matcher",
                      "colmap.mapper", "colmap.bundle_adjuster"],
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body == {"valid": True, "errors": []}


async def test_type_break_is_reported() -> None:
    async with await _client() as client:
        r = await client.post("/v1/pipelines:validate", json={
            "steps": ["colmap.feature_extractor", "colmap.mapper"],
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["valid"] is False
        assert len(body["errors"]) == 1
        assert "no shared type" in body["errors"][0]["message"]
        assert body["errors"][0]["where"] == "step 0->1"
