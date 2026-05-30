"""POST /v1/pipelines:validate -- typed-dataflow pre-flight type-checking."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.anyio


async def _client() -> AsyncClient:
    from app.main import create_app

    return AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://t")


async def test_datatypes_discovery() -> None:
    async with await _client() as client:
        r = await client.get("/v1/datatypes")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["contract"] == "datatypes"
        ids = {t["type_id"] for t in body["types"]}
        assert {"image_sequence", "feature_set", "sparse_model"} <= ids


async def test_operations_discovery() -> None:
    async with await _client() as client:
        r = await client.get("/v1/operations")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["contract"] == "operations"
        ops = {o["op_id"]: o for o in body["operations"]}
        assert ops["features"]["consumes"] == ["image_sequence"]
        assert ops["features"]["capabilities"] == ["features.extract"]


async def test_valid_pipeline_passes() -> None:
    async with await _client() as client:
        r = await client.post("/v1/pipelines:validate", json={
            "steps": ["features", "pairs", "matches", "verify", "map"],
        })
        assert r.status_code == 200, r.text
        assert r.json() == {"valid": True, "errors": []}


async def test_missing_input_is_reported() -> None:
    async with await _client() as client:
        r = await client.post("/v1/pipelines:validate", json={
            "steps": ["features", "map"],
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["valid"] is False
        assert len(body["errors"]) == 1
        assert "missing input(s): match_graph" in body["errors"][0]["message"]
        assert body["errors"][0]["where"] == "step 1 'map'"
