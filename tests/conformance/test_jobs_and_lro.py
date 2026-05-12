"""§3.9 LRO + §6.6 Stages + §6.7 Jobs + §9 cache + cancel."""

from __future__ import annotations

import pytest

from tests.conformance.conftest import make_project_dataset

pytestmark = pytest.mark.conformance


async def test_features_returns_202_with_location(conf_client) -> None:
    """§3.9 LROs MUST return 202 with a `Location` header pointing at
    the job."""
    _pid, did, _ = await make_project_dataset(conf_client, name="lro")
    resp = await conf_client.post(f"/v1/datasets/{did}/features", json={"spec": {"use_gpu": False}})
    assert resp.status_code == 202, resp.text
    assert resp.headers.get("location", "").startswith("/v1/jobs/")
    body = resp.json()
    assert "job_id" in body
    assert isinstance(body.get("task_ids"), list)


async def test_features_rejects_empty_dataset(conf_client) -> None:
    """A dataset with no images MUST be rejected up front (§6.6)."""
    pr = await conf_client.post("/v1/projects", json={"name": "empty-ds"})
    pid = pr.json()["project_id"]
    ds = await conf_client.post(
        f"/v1/projects/{pid}/datasets",
        json={"name": "empty", "source": {"kind": "upload", "entries": []}},
    )
    did = ds.json()["dataset_id"]
    resp = await conf_client.post(f"/v1/datasets/{did}/features", json={"spec": {}})
    assert resp.status_code == 422


async def test_job_get_returns_taskdetail_shape(conf_client) -> None:
    _pid, did, _ = await make_project_dataset(conf_client, name="jobshape")
    sub = await conf_client.post(f"/v1/datasets/{did}/features", json={"spec": {"use_gpu": False}})
    job_id = sub.json()["job_id"]
    detail = await conf_client.get(f"/v1/jobs/{job_id}")
    assert detail.status_code == 200
    body = detail.json()
    for k in ("job_id", "tenant_id", "project_id", "recipe", "status", "tasks"):
        assert k in body, f"job detail missing {k!r}"
    assert isinstance(body["tasks"], list)
    assert body["tasks"], "tasks should be non-empty"
    t = body["tasks"][0]
    for k in ("task_id", "kind", "status", "cache_key", "inputs_hash", "params_hash"):
        assert k in t, f"task missing {k!r}"


async def test_cancel_sets_flag(conf_client) -> None:
    _pid, did, _ = await make_project_dataset(conf_client, name="cancel")
    sub = await conf_client.post(f"/v1/datasets/{did}/features", json={"spec": {"use_gpu": False}})
    job_id = sub.json()["job_id"]
    cancel = await conf_client.post(f"/v1/jobs/{job_id}:cancel")
    assert cancel.status_code == 200
    body = cancel.json()
    assert body["cancel_requested"] is True


async def test_cache_short_circuit_on_identical_inputs(conf_client) -> None:
    """§9.1 Identical (kind, inputs_hash, params_hash, runtime_version_id)
    MUST short-circuit to the cached output without re-running."""
    _pid, did, _ = await make_project_dataset(conf_client, name="cache")
    spec = {"use_gpu": False, "max_num_features": 4096}
    a = await conf_client.post(f"/v1/datasets/{did}/features", json={"spec": spec})
    b = await conf_client.post(f"/v1/datasets/{did}/features", json={"spec": spec})
    assert a.status_code == 202
    assert b.status_code == 202
    job_a = (await conf_client.get(f"/v1/jobs/{a.json()['job_id']}")).json()
    job_b = (await conf_client.get(f"/v1/jobs/{b.json()['job_id']}")).json()
    cache_a = job_a["tasks"][0]["cache_key"]
    cache_b = job_b["tasks"][0]["cache_key"]
    assert cache_a == cache_b, "identical specs MUST yield identical cache_key"
