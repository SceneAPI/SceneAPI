"""POST /v1/projects/{pid}/pipelines:run -- legacy and typed pipelines.

The legacy flat SfM operation chain is still an executable v1 submission
shape. Native typed Processor DAG wiring is checked before any job is created:
type-invalid payloads fail 422 and type-valid native DAGs currently fail 501
because the generic typed processor executor has not landed yet.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e


def _install_typed_plugin(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import dataflow_registry_service
    from sfm_hub.models import PluginManifest
    from sfm_hub.state import record_manual_install

    manifest = PluginManifest.model_validate(
        {
            "schema_version": 1,
            "plugin_id": "typed",
            "display_name": "Typed test plugin",
            "description": "Typed-dataflow extension fixture.",
            "package_name": "typed-plugin",
            "github_url": "https://github.com/example/typed-plugin",
            "entry_points": ["typed_plugin:plugin"],
            "providers": [
                {
                    "provider_id": "typed",
                    "display_name": "Typed",
                    "capabilities": ["radiance.train"],
                }
            ],
            "runtime_modes": {
                "uv": {
                    "url": "https://github.com/example/typed-plugin",
                    "package": "typed-plugin",
                }
            },
            "capabilities": ["radiance.train"],
            "datatypes": [
                {
                    "type_id": "typed_field",
                    "title": "Typed field",
                    "kind": "artifact",
                    "description": "Plugin-owned field.",
                }
            ],
            "processors": [
                {
                    "processor_id": "train",
                    "title": "Typed train",
                    "consumer": {"model": {"datatype": "sparse_model"}},
                    "supplier": {"field": {"datatype": "typed_field"}},
                    "attributes": [
                        {
                            "name": "method",
                            "type": "enum",
                            "enum": ["splat"],
                            "default": "splat",
                        }
                    ],
                    "capabilities": ["radiance.train"],
                }
            ],
        }
    )
    monkeypatch.setattr(
        dataflow_registry_service.plugin_registry,
        "list_manifests",
        lambda: [manifest],
    )
    monkeypatch.setattr(
        dataflow_registry_service.discovery,
        "discovered_plugin_ids",
        lambda: set(),
    )
    record_manual_install("typed", method="uv", enabled=True)


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
    did = ds.json()["dataset_id"]
    await client.post(f"/v1/datasets/{did}/images", json={"name": "a.jpg", "blob_sha": sha})
    return pid, did


async def test_valid_legacy_pipeline_submits_job(client) -> None:
    pid, did = await _setup(client)
    resp = await client.post(
        f"/v1/projects/{pid}/pipelines:run",
        json={
            "dataset_id": did,
            "steps": [
                {"op": "features"},
                {"op": "pairs"},
                {"op": "matches"},
                {"op": "verify"},
                {"op": "map"},
            ],
        },
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["job_id"]
    assert body["project_id"] == pid
    assert body["dataset_id"] == did
    assert body["recon_id"]
    assert len(body["task_ids"]) == 4


async def test_valid_legacy_string_pipeline_submits_job(client) -> None:
    pid, did = await _setup(client)
    resp = await client.post(
        f"/v1/projects/{pid}/pipelines:run",
        json={"dataset_id": did, "steps": ["features", "pairs", "matches", "verify", "map"]},
    )
    assert resp.status_code == 202, resp.text
    assert len(resp.json()["task_ids"]) == 4


async def test_initial_inputs_seed_partial_pipeline_until_executor_exists(client) -> None:
    pid, did = await _setup(client)
    resp = await client.post(
        f"/v1/projects/{pid}/pipelines:run",
        json={
            "dataset_id": did,
            "initial_inputs": ["sparse_model"],
            "steps": [{"ref": "refine", "processor": "refine"}],
        },
    )
    assert resp.status_code == 501, resp.text
    assert resp.json()["capability"] == "pipelines.custom_execution"


async def test_plugin_processor_is_validated_before_executor_501(
    client,
    monkeypatch,
) -> None:
    _install_typed_plugin(monkeypatch)
    pid, did = await _setup(client)
    resp = await client.post(
        f"/v1/projects/{pid}/pipelines:run",
        json={
            "dataset_id": did,
            "initial_inputs": ["sparse_model"],
            "steps": [
                {
                    "ref": "train",
                    "processor": "typed.train",
                    "attributes": {"method": "splat"},
                }
            ],
        },
    )
    assert resp.status_code == 501, resp.text
    assert resp.json()["capability"] == "pipelines.custom_execution"


async def test_dotted_attribute_error_loc_preserves_attribute_name(client) -> None:
    pid, did = await _setup(client)
    resp = await client.post(
        f"/v1/projects/{pid}/pipelines:run",
        json={
            "dataset_id": did,
            "steps": [{"processor": "features", "attributes": {"plugin.weight": 1}}],
        },
    )
    assert resp.status_code == 422
    assert resp.json()["errors"][0]["loc"] == [
        "body",
        "steps",
        0,
        "attributes",
        "plugin.weight",
    ]


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


async def test_empty_steps_are_rejected_before_project_lookup(client) -> None:
    resp = await client.post(
        "/v1/projects/not-a-real-project/pipelines:run",
        json={"dataset_id": "not-a-real-dataset", "steps": []},
    )
    assert resp.status_code == 422
    assert "at least 1 item" in resp.json()["detail"]


async def test_params_are_attribute_validated_before_submit(client) -> None:
    pid, did = await _setup(client)
    resp = await client.post(
        f"/v1/projects/{pid}/pipelines:run",
        json={
            "dataset_id": did,
            "steps": [{"op": "features", "params": {"type": "bogus"}}],
        },
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["errors"][0]["type"] == "invalid_attribute"
    assert body["errors"][0]["ctx"]["reason"] == "invalid_attribute"
    assert body["errors"][0]["ctx"]["path"] == "steps.0.attributes.type"


async def test_valid_legacy_params_keep_flat_wiring_then_submit(client) -> None:
    pid, did = await _setup(client)
    resp = await client.post(
        f"/v1/projects/{pid}/pipelines:run",
        json={
            "dataset_id": did,
            "steps": [
                {"op": "features", "params": {"type": "sift"}},
                {"op": "pairs"},
                {"op": "matches"},
                {"op": "verify"},
                {"op": "map"},
            ],
        },
    )
    assert resp.status_code == 202, resp.text
    assert len(resp.json()["task_ids"]) == 4


async def test_valid_legacy_alias_params_keep_recipe_projection(client) -> None:
    pid, did = await _setup(client)
    resp = await client.post(
        f"/v1/projects/{pid}/pipelines:run",
        json={
            "dataset_id": did,
            "steps": [
                {
                    "op": "features",
                    "params": {
                        "type": "sift",
                        "sift_max_num_features": 512,
                        "sift_first_octave": 0,
                    },
                },
                {"op": "pairs"},
                {"op": "matches"},
                {"op": "verify"},
                {"op": "map", "params": {"max_num_models": 2}},
            ],
        },
    )
    assert resp.status_code == 202, resp.text
    assert len(resp.json()["task_ids"]) == 4


async def test_executable_legacy_provider_selector_flows_into_recipe(client) -> None:
    pid, did = await _setup(client)
    resp = await client.post(
        f"/v1/projects/{pid}/pipelines:run",
        json={
            "dataset_id": did,
            "steps": [
                {"op": "features", "provider": "colmap_cli"},
                {"op": "pairs"},
                {"op": "matches", "provider": "colmap_cli"},
                {"op": "verify"},
                {"op": "map", "provider": "colmap_cli"},
            ],
        },
    )
    assert resp.status_code == 202, resp.text
    assert len(resp.json()["task_ids"]) == 4


async def test_provider_selector_is_rejected_until_executor_exists(client) -> None:
    pid, did = await _setup(client)
    resp = await client.post(
        f"/v1/projects/{pid}/pipelines:run",
        json={
            "dataset_id": did,
            "steps": [{"op": "features", "provider": "colmap_cli"}],
        },
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["errors"][0]["type"] == "provider_unsupported"
    assert body["errors"][0]["ctx"]["reason"] == "provider_unsupported"
    assert body["errors"][0]["ctx"]["path"] == "steps.0.provider"


async def test_native_provider_selector_reaches_executor_gate(client) -> None:
    pid, did = await _setup(client)
    resp = await client.post(
        f"/v1/projects/{pid}/pipelines:run",
        json={
            "dataset_id": did,
            "steps": [{"processor": "features", "provider": "colmap_cli"}],
        },
    )
    assert resp.status_code == 501, resp.text
    assert resp.json()["capability"] == "pipelines.custom_execution"


async def test_empty_provider_selector_is_rejected_before_submit(client) -> None:
    pid, did = await _setup(client)
    resp = await client.post(
        f"/v1/projects/{pid}/pipelines:run",
        json={
            "dataset_id": did,
            "steps": [{"op": "features", "provider": ""}],
        },
    )
    assert resp.status_code == 422
    assert "at least 1 character" in resp.json()["detail"]


async def test_processor_steps_use_named_port_graph_not_legacy_projection(client) -> None:
    pid, did = await _setup(client)
    resp = await client.post(
        f"/v1/projects/{pid}/pipelines:run",
        json={
            "dataset_id": did,
            "steps": [
                {"processor": "features"},
                {"processor": "pairs"},
                {"processor": "matches"},
                {"processor": "verify"},
                {"processor": "map"},
            ],
        },
    )
    assert resp.status_code == 422
    assert "ambiguous input" in resp.text


async def test_unknown_dataset_is_rejected_before_submit(client) -> None:
    pr = await client.post("/v1/projects", json={"name": "p-run-missing-dataset"})
    pid = pr.json()["project_id"]
    resp = await client.post(
        f"/v1/projects/{pid}/pipelines:run",
        json={"dataset_id": "00000000000000000000000000", "steps": [{"op": "features"}]},
    )
    assert resp.status_code == 404
    assert "Dataset 00000000000000000000000000 not found" in resp.text


async def test_project_scoped_run_rejects_dataset_from_other_project(client) -> None:
    _pid, did = await _setup(client)
    other = await client.post("/v1/projects", json={"name": "p-run-other"})
    other_pid = other.json()["project_id"]
    resp = await client.post(
        f"/v1/projects/{other_pid}/pipelines:run",
        json={"dataset_id": did, "steps": [{"op": "features"}]},
    )
    assert resp.status_code == 422
    assert "Dataset does not belong to project" in resp.text
