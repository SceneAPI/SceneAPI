"""POST /v1/projects/{pid}/pipelines:run -- custom typed-operation pipeline.

The operation sequence is type-checked before any job is created, so the typed
model guards real submissions (unlike the fixed recipes, an arbitrary pipeline
can be invalid).
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e


async def _setup(client) -> tuple[str, str]:
    pr = await client.post("/v1/projects", json={"name": "p-run"})
    pid = pr.json()["project_id"]
    payload = b"\xff\xd8\xff\xe0imagebytes"
    init = await client.post("/v1/uploads", json={"expected_size": len(payload)})
    uid = init.json()["upload_id"]
    await client.patch(
        f"/v1/uploads/{uid}",
        content=payload,
        headers={"Content-Range": f"bytes 0-{len(payload) - 1}/{len(payload)}"},
    )
    sha = (await client.post(f"/v1/uploads/{uid}:finalize")).json()["blob_sha"]
    ds = await client.post(
        f"/v1/projects/{pid}/datasets",
        json={
            "name": "ds",
            "source": {"kind": "upload", "entries": [{"name": "a.jpg", "blob_sha": sha}]},
        },
    )
    return pid, ds.json()["dataset_id"]


async def test_valid_pipeline_submits(client) -> None:
    pid, did = await _setup(client)
    resp = await client.post(
        f"/v1/projects/{pid}/pipelines:run",
        json={
            "dataset_id": did,
            "steps": [
                {"op": "features"}, {"op": "pairs"}, {"op": "matches"},
                {"op": "verify"}, {"op": "map"},
            ],
        },
    )
    assert resp.status_code == 202, resp.text
    assert len(resp.json()["task_ids"]) == 5


async def test_type_break_is_rejected_before_submit(client) -> None:
    pid, did = await _setup(client)
    resp = await client.post(
        f"/v1/projects/{pid}/pipelines:run",
        json={"dataset_id": did, "steps": [{"op": "features"}, {"op": "map"}]},
    )
    assert resp.status_code == 422
    assert "match_graph" in resp.text  # map's missing input


async def test_unknown_operation_is_rejected(client) -> None:
    pid, did = await _setup(client)
    resp = await client.post(
        f"/v1/projects/{pid}/pipelines:run",
        json={"dataset_id": did, "steps": [{"op": "frobnicate"}]},
    )
    assert resp.status_code == 422
    assert "frobnicate" in resp.text
