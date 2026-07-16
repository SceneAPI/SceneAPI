from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e


async def _upload(client, payload: bytes) -> str:
    init = await client.post("/v1/uploads", json={"expected_size": len(payload)})
    upload_id = init.json()["upload_id"]
    await client.patch(
        f"/v1/uploads/{upload_id}",
        content=payload,
        headers={"Content-Range": f"bytes 0-{len(payload) - 1}/{len(payload)}"},
    )
    fin = await client.post(f"/v1/uploads/{upload_id}:finalize")
    return fin.json()["blob_sha"]


async def _setup(client) -> tuple[str, str]:
    pr = await client.post("/v1/projects", json={"name": "p-pipe"})
    pid = pr.json()["project_id"]
    sha = await _upload(client, b"\xff\xd8\xff\xe0imagebytes")
    ds = await client.post(
        f"/v1/projects/{pid}/datasets",
        json={
            "name": "ds",
            "source": {"kind": "upload", "entries": [{"name": "a.jpg", "blob_sha": sha}]},
        },
    )
    did = ds.json()["dataset_id"]
    await client.post(f"/v1/datasets/{did}/images", json={"name": "a.jpg", "blob_sha": sha})
    return pid, did


async def test_recipe_kind_must_match_path(client) -> None:
    pid, did = await _setup(client)
    resp = await client.post(
        f"/v1/projects/{pid}/pipelines/incremental",
        json={
            "dataset_id": did,
            "spec": {"kind": "global"},  # mismatch
        },
    )
    assert resp.status_code == 422


async def test_recipe_rejects_dataset_from_other_project(client) -> None:
    _pid, did = await _setup(client)
    other = await client.post("/v1/projects", json={"name": "p-pipe-other"})
    other_pid = other.json()["project_id"]

    resp = await client.post(
        f"/v1/projects/{other_pid}/pipelines/incremental",
        json={"dataset_id": did, "spec": {"kind": "incremental"}},
    )

    assert resp.status_code == 422
    assert "Dataset does not belong to project" in resp.text


async def test_incremental_recipe_creates_4_node_dag(client) -> None:
    pid, did = await _setup(client)
    resp = await client.post(
        f"/v1/projects/{pid}/pipelines/incremental",
        json={"dataset_id": did, "spec": {"kind": "incremental"}},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert "job_id" in body
    assert "recon_id" in body
    assert len(body["task_ids"]) == 4

    detail = await client.get(f"/v1/jobs/{body['job_id']}")
    j = detail.json()
    kinds = sorted(t["kind"] for t in j["tasks"])
    assert kinds == ["extract", "map", "match", "verify"]


async def test_global_and_spherical_recipes_succeed(client) -> None:
    pid, did = await _setup(client)
    for kind in ("global", "hierarchical", "spherical"):
        resp = await client.post(
            f"/v1/projects/{pid}/pipelines/{kind}",
            json={"dataset_id": did, "spec": {"kind": kind}},
        )
        assert resp.status_code == 202, (kind, resp.text)
