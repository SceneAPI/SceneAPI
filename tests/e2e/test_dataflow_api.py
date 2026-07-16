"""POST /v1/pipelines:validate -- typed-dataflow pre-flight type-checking."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError as PydanticValidationError

pytestmark = pytest.mark.anyio


async def _client() -> AsyncClient:
    from app.main import create_app

    return AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://t")


def _install_typed_plugin(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import dataflow_registry_service
    from sfm_hub.models import PluginManifest
    from sfm_hub.state import record_manual_install

    manifest = PluginManifest.model_validate({
        "schema_version": 1,
        "plugin_id": "typed",
        "display_name": "Typed test plugin",
        "description": "Typed-dataflow extension fixture.",
        "package_name": "typed-plugin",
        "github_url": "https://github.com/example/typed-plugin",
        "entry_points": ["typed_plugin:plugin"],
        "providers": [{
            "provider_id": "typed",
            "display_name": "Typed",
            "capabilities": ["features.extract.sift", "radiance.train"],
        }],
        "runtime_modes": {
            "uv": {
                "url": "https://github.com/example/typed-plugin",
                "package": "typed-plugin",
            }
        },
        "capabilities": ["features.extract.sift", "radiance.train"],
        "datatypes": [
            {
                "type_id": "typed_field",
                "title": "Typed field",
                "kind": "artifact",
                "description": "Plugin-owned field.",
            },
            {
                "type_id": "typed_mask",
                "title": "Typed mask",
                "kind": "scene_input",
                "description": "Plugin-owned optional mask input.",
            },
            {
                "type_id": "typed_train_mask",
                "title": "Typed train mask",
                "kind": "scene_input",
                "description": "Plugin-owned optional training mask input.",
            },
        ],
        "processors": [
            {
                "processor_id": "train",
                "title": "Typed train",
                "consumer": {"model": {"datatype": "sparse_model"}},
                "supplier": {"field": {"datatype": "typed_field"}},
                "attributes": [{
                    "name": "method",
                    "type": "enum",
                    "enum": ["splat"],
                    "default": "splat",
                }, {
                    "name": "output_type",
                    "type": "datatype-ref",
                    "default": "typed_field",
                }],
                "capabilities": ["radiance.train"],
            },
            {
                "processor_id": "collect",
                "title": "Typed field collector",
                "consumer": {
                    "field": {
                        "datatype": "typed_field",
                        "required": False,
                        "multiple": True,
                    }
                },
                "supplier": {"field": {"datatype": "typed_field"}},
                "capabilities": ["radiance.train"],
            },
        ],
        "processor_extensions": [{
            "processor_id": "features",
            "special_inputs": {
                "typed.mask": {
                    "datatype": "typed_mask",
                    "required": False,
                    "description": "Optional plugin mask.",
                },
            },
            "special_attributes": [{
                "name": "typed.weight",
                "type": "float",
                "default": 1.0,
                "min": 0.0,
            }],
        }, {
            "processor_id": "train",
            "special_inputs": {
                "typed.train_mask": {
                    "datatype": "typed_train_mask",
                    "required": False,
                    "description": "Optional plugin training mask.",
                },
            },
            "special_attributes": [{
                "name": "typed.temperature",
                "type": "float",
                "default": 0.25,
                "min": 0.0,
            }],
        }],
        "pipelines": [
            {
                "pipeline_id": "radiance_from_sparse",
                "title": "Radiance from sparse model",
                "initial_inputs": ["sparse_model", "typed_train_mask"],
                "steps": [{
                    "ref": "train",
                    "processor": "train",
                    "attributes": {
                        "method": "splat",
                        "output_type": "typed_field",
                        "typed.temperature": 0.5,
                    },
                    "wires": {
                        "model": "inputs.sparse_model",
                        "typed.train_mask": "inputs.typed_train_mask",
                    },
                }],
            },
            {
                "pipeline_id": "collect_field",
                "title": "Collect field",
                "initial_inputs": ["sparse_model"],
                "steps": [
                    {
                        "ref": "train",
                        "processor": "train",
                        "attributes": {"method": "splat"},
                        "wires": {"model": "inputs.sparse_model"},
                    },
                    {
                        "ref": "collect",
                        "processor": "collect",
                        "wires": {"field": "train.field"},
                    },
                ],
            },
        ],
    })
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


async def test_processors_discovery() -> None:
    async with await _client() as client:
        r = await client.get("/v1/processors")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["contract"] == "processors"
        processors = {p["processor_id"]: p for p in body["processors"]}
        assert processors["features"]["consumer"]["images"]["datatype"] == "image_sequence"
        assert processors["map"]["supplier"]["model"]["datatype"] == "sparse_model"
        assert processors["features"]["attributes"][0]["name"] == "type"


async def test_attributes_discovery() -> None:
    async with await _client() as client:
        r = await client.get("/v1/attributes")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["contract"] == "attributes"
        assert "enum" in body["attribute_types"]


async def test_pipelines_discovery() -> None:
    async with await _client() as client:
        r = await client.get("/v1/pipelines")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["contract"] == "pipelines"
        assert body["canonical_pipelines"]["sfm"] == [
            "features",
            "pairs",
            "matches",
            "verify",
            "map",
        ]
        assert body["plugin_pipelines"] == []


async def test_installed_plugin_extensions_are_effective(monkeypatch) -> None:
    _install_typed_plugin(monkeypatch)
    async with await _client() as client:
        datatypes = (await client.get("/v1/datatypes")).json()
        datatypes_by_id = {row["type_id"]: row for row in datatypes["types"]}
        assert "typed.typed_field" in datatypes_by_id
        assert "typed_train_mask" in datatypes_by_id["typed.typed_train_mask"]["aliases"]

        processors = (await client.get("/v1/processors")).json()
        by_id = {row["processor_id"]: row for row in processors["processors"]}
        assert by_id["typed.train"]["supplier"]["field"]["datatype"] == "typed.typed_field"
        assert "train" in by_id["typed.train"]["aliases"]
        attrs = {attr["name"]: attr for attr in by_id["typed.train"]["attributes"]}
        assert attrs["output_type"]["default"] == "typed.typed_field"
        assert (
            by_id["typed.train"]["special_inputs"]["typed.train_mask"]["datatype"]
            == "typed.typed_train_mask"
        )
        assert {
            attr["name"] for attr in by_id["typed.train"]["special_attributes"]
        } == {"typed.temperature"}
        assert (
            by_id["features"]["special_inputs"]["typed.mask"]["datatype"]
            == "typed.typed_mask"
        )

        pipeline_contract = (await client.get("/v1/pipelines")).json()
        pipelines_by_id = {
            row["pipeline_id"]: row for row in pipeline_contract["plugin_pipelines"]
        }
        pipeline = pipelines_by_id["typed.radiance_from_sparse"]
        assert "radiance_from_sparse" in pipeline["aliases"]
        assert pipeline["initial_inputs"] == [
            "sparse_model",
            "typed.typed_train_mask",
        ]
        assert pipeline["steps"][0]["processor"] == "typed.train"
        assert pipeline["steps"][0]["attributes"]["output_type"] == "typed.typed_field"
        assert pipeline["steps"][0]["wires"]["typed.train_mask"] == (
            "inputs.typed.typed_train_mask"
        )
        collect_pipeline = pipelines_by_id["typed.collect_field"]
        assert collect_pipeline["steps"][1]["wires"]["field"] == ["train.field"]

        train = await client.post("/v1/pipelines:validate", json={
            "initial_inputs": ["sparse_model", "typed.typed_train_mask"],
            "steps": [{
                "ref": "train",
                "processor": "typed.train",
                "attributes": {
                    "method": "splat",
                    "output_type": "typed.typed_field",
                    "typed.temperature": 0.5,
                },
                "wires": {
                    "model": "inputs.sparse_model",
                    "typed.train_mask": "inputs.typed.typed_train_mask",
                },
            }],
        })
        assert train.status_code == 200, train.text
        assert train.json() == {"valid": True, "errors": []}

        alias_train = await client.post("/v1/pipelines:validate", json={
            "initial_inputs": ["sparse_model", "typed_train_mask"],
            "steps": [{
                "ref": "train",
                "processor": "typed.train",
                "attributes": {"method": "splat", "typed.temperature": 0.5},
                "wires": {
                    "model": "inputs.sparse_model",
                    "typed.train_mask": "inputs.typed_train_mask",
                },
            }],
        })
        assert alias_train.status_code == 200, alias_train.text
        assert alias_train.json() == {"valid": True, "errors": []}

        extended = await client.post("/v1/pipelines:validate", json={
            "initial_inputs": ["image_sequence", "typed.typed_mask"],
            "steps": [{
                "ref": "extract",
                "processor": "features",
                "attributes": {"typed.weight": 0.5},
                "wires": {
                    "images": "inputs.image_sequence",
                    "typed.mask": "inputs.typed.typed_mask",
                },
            }],
        })
        assert extended.status_code == 200, extended.text
        assert extended.json() == {"valid": True, "errors": []}


async def test_legacy_plugin_processor_shape_is_not_discoverable_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_typed_plugin(monkeypatch)
    async with await _client() as client:
        r = await client.post("/v1/pipelines:validate", json={
            "initial_inputs": ["sparse_model"],
            "steps": [{"op": "typed.train", "params": {"method": "splat"}}],
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["valid"] is False
        assert body["errors"][0]["reason"] == "unknown_processor"
        assert body["errors"][0]["path"] == "steps.0.op"


async def test_active_plugin_registry_rejects_duplicate_plugin_ids(monkeypatch) -> None:
    from app.services import dataflow_registry_service
    from sfm_hub.models import PluginManifest
    from sfm_hub.state import record_manual_install

    def manifest(plugin_id: str) -> PluginManifest:
        return PluginManifest.model_validate({
            "schema_version": 1,
            "plugin_id": plugin_id,
            "display_name": plugin_id,
            "description": "Duplicate registry fixture.",
            "package_name": plugin_id,
            "github_url": f"https://github.com/example/{plugin_id}",
            "entry_points": [f"{plugin_id}:plugin"],
            "providers": [{
                "provider_id": plugin_id,
                "display_name": plugin_id,
                "capabilities": ["features.extract.sift"],
            }],
            "runtime_modes": {
                "uv": {
                    "url": f"https://github.com/example/{plugin_id}",
                    "package": plugin_id,
                }
            },
            "capabilities": ["features.extract.sift"],
            "datatypes": [{
                "type_id": "duplicate_field",
                "title": "Duplicate field",
                "kind": "artifact",
            }],
        })

    monkeypatch.setattr(
        dataflow_registry_service.plugin_registry,
        "list_manifests",
        lambda: [manifest("dup"), manifest("dup")],
    )
    monkeypatch.setattr(
        dataflow_registry_service.discovery,
        "discovered_plugin_ids",
        lambda: set(),
    )
    record_manual_install("dup", method="uv", enabled=True)

    async with await _client() as client:
        r = await client.get("/v1/datatypes")
        assert r.status_code == 422
        body = r.json()
        assert body["type"].endswith("/validation")
        assert "duplicate active plugin id" in body["detail"]


async def test_plugin_local_datatype_ids_are_canonicalized_per_plugin(
    monkeypatch,
) -> None:
    from app.services import dataflow_registry_service
    from sfm_hub.models import PluginManifest
    from sfm_hub.state import record_manual_install

    def manifest(plugin_id: str) -> PluginManifest:
        return PluginManifest.model_validate({
            "schema_version": 1,
            "plugin_id": plugin_id,
            "display_name": plugin_id,
            "description": "Canonical registry fixture.",
            "package_name": plugin_id,
            "github_url": f"https://github.com/example/{plugin_id}",
            "entry_points": [f"{plugin_id}:plugin"],
            "providers": [{
                "provider_id": plugin_id,
                "display_name": plugin_id,
                "capabilities": ["features.extract.sift"],
            }],
            "runtime_modes": {
                "uv": {
                    "url": f"https://github.com/example/{plugin_id}",
                    "package": plugin_id,
                }
            },
            "capabilities": ["features.extract.sift"],
            "datatypes": [{
                "type_id": "local_field",
                "title": "Local field",
                "kind": "artifact",
            }],
            "processors": [{
                "processor_id": "producer",
                "title": "Producer",
                "consumer": {"images": {"datatype": "image_sequence"}},
                "supplier": {"field": {"datatype": "local_field"}},
                "capabilities": ["features.extract.sift"],
            }],
        })

    monkeypatch.setattr(
        dataflow_registry_service.plugin_registry,
        "list_manifests",
        lambda: [manifest("pluga"), manifest("plugb")],
    )
    monkeypatch.setattr(
        dataflow_registry_service.discovery,
        "discovered_plugin_ids",
        lambda: set(),
    )
    record_manual_install("pluga", method="uv", enabled=True)
    record_manual_install("plugb", method="uv", enabled=True)

    async with await _client() as client:
        datatypes = (await client.get("/v1/datatypes")).json()
        assert {"pluga.local_field", "plugb.local_field"} <= {
            row["type_id"] for row in datatypes["types"]
        }

        processors = (await client.get("/v1/processors")).json()
        by_id = {row["processor_id"]: row for row in processors["processors"]}
        assert by_id["pluga.producer"]["supplier"]["field"]["datatype"] == (
            "pluga.local_field"
        )
        assert by_id["plugb.producer"]["supplier"]["field"]["datatype"] == (
            "plugb.local_field"
        )


async def test_plugin_owned_dotted_datatype_id_fails_closed() -> None:
    from sfm_hub.models import PluginManifest

    with pytest.raises(PydanticValidationError):
        PluginManifest.model_validate({
            "schema_version": 1,
            "plugin_id": "owner",
            "display_name": "Owner",
            "description": "Foreign-prefix registry fixture.",
            "package_name": "owner",
            "github_url": "https://github.com/example/owner",
            "entry_points": ["owner:plugin"],
            "providers": [{
                "provider_id": "owner",
                "display_name": "owner",
                "capabilities": ["features.extract.sift"],
            }],
            "runtime_modes": {
                "uv": {
                    "url": "https://github.com/example/owner",
                    "package": "owner",
                }
            },
            "capabilities": ["features.extract.sift"],
            "datatypes": [{
                "type_id": "other.field",
                "title": "Foreign field",
                "kind": "artifact",
            }],
        })


async def test_valid_pipeline_passes() -> None:
    async with await _client() as client:
        r = await client.post("/v1/pipelines:validate", json={
            "steps": ["features", "pairs", "matches", "verify", "map"],
        })
        assert r.status_code == 200, r.text
        assert r.json() == {"valid": True, "errors": []}


async def test_valid_processor_pipeline_passes() -> None:
    async with await _client() as client:
        r = await client.post("/v1/pipelines:validate", json={
            "steps": [
                {"ref": "extract", "processor": "features"},
                {"ref": "pair", "processor": "pairs",
                 "wires": {"features": "extract.features"}},
                {"ref": "match", "processor": "matches", "wires": {
                    "features": "extract.features",
                    "pairs": "pair.pairs",
                }},
                {"ref": "verify", "processor": "verify",
                 "wires": {"matches": "match.matches"}},
                {"ref": "map", "processor": "map", "wires": {
                    "features": "extract.features",
                    "matches": "verify.matches",
                }},
            ],
        })
        assert r.status_code == 200, r.text
        assert r.json() == {"valid": True, "errors": []}


async def test_processor_pipeline_rejects_malformed_step_ref() -> None:
    async with await _client() as client:
        r = await client.post("/v1/pipelines:validate", json={
            "steps": [{"ref": "bad.ref", "processor": "features"}],
        })
        assert r.status_code == 422
        body = r.json()
        assert any(
            error["type"] == "string_pattern_mismatch" and error["loc"][-1] == "ref"
            for error in body["errors"]
        )


async def test_pipeline_step_provider_rejects_overlong_selector_component() -> None:
    async with await _client() as client:
        r = await client.post("/v1/pipelines:validate", json={
            "steps": [{"op": "features", "provider": "p" * 65}],
        })
        assert r.status_code == 422
        body = r.json()
        assert any(
            error["type"] == "string_pattern_mismatch"
            and error["loc"][-1] == "provider"
            for error in body["errors"]
        )


async def test_initial_inputs_can_seed_partial_pipeline() -> None:
    async with await _client() as client:
        r = await client.post("/v1/pipelines:validate", json={
            "initial_inputs": ["sparse_model"],
            "steps": [{"ref": "refine", "processor": "refine"}],
        })
        assert r.status_code == 200, r.text
        assert r.json() == {"valid": True, "errors": []}


async def test_initial_inputs_validate_datatype_ids_and_duplicates() -> None:
    async with await _client() as client:
        r = await client.post("/v1/pipelines:validate", json={
            "initial_inputs": ["sparse_model", "sparse_model", "bogus_type"],
            "steps": [{"ref": "refine", "processor": "refine"}],
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["valid"] is False
        reasons = {error["reason"] for error in body["errors"]}
        assert {"duplicate_initial_input", "unknown_datatype"} <= reasons


async def test_ambiguous_processor_input_is_reported() -> None:
    async with await _client() as client:
        r = await client.post("/v1/pipelines:validate", json={
            "steps": [
                {"ref": "extract", "processor": "features"},
                {"ref": "pair", "processor": "pairs",
                 "wires": {"features": "extract.features"}},
                {"ref": "match", "processor": "matches", "wires": {
                    "features": "extract.features",
                    "pairs": "pair.pairs",
                }},
                {"ref": "verify", "processor": "verify",
                 "wires": {"matches": "match.matches"}},
                {"ref": "map", "processor": "map",
                 "wires": {"features": "extract.features"}},
            ],
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["valid"] is False
        assert body["errors"][0]["reason"] == "ambiguous_input"
        assert body["errors"][0]["path"] == "steps.4.wires.matches"


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


async def test_validate_rejects_malformed_envelope() -> None:
    async with await _client() as client:
        r = await client.post("/v1/pipelines:validate", json={})
        assert r.status_code == 422
        assert "body.steps" in r.json()["detail"]

        r = await client.post("/v1/pipelines:validate", json={"steps": []})
        assert r.status_code == 422
        assert "at least 1 item" in r.json()["detail"]


async def test_params_promote_legacy_step_to_attribute_validation() -> None:
    async with await _client() as client:
        r = await client.post("/v1/pipelines:validate", json={
            "steps": [{"op": "features", "params": {"type": "bogus"}}],
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["valid"] is False
        assert body["errors"][0]["reason"] == "invalid_attribute"
        assert body["errors"][0]["path"] == "steps.0.attributes.type"


async def test_legacy_pipeline_with_valid_params_keeps_flat_wiring() -> None:
    async with await _client() as client:
        r = await client.post("/v1/pipelines:validate", json={
            "steps": [
                {"op": "features", "params": {"type": "sift"}},
                {"op": "pairs"}, {"op": "matches"}, {"op": "verify"},
                {"op": "map"},
            ],
        })
        assert r.status_code == 200, r.text
        assert r.json() == {"valid": True, "errors": []}


async def test_executable_legacy_pipeline_allows_provider_selectors() -> None:
    async with await _client() as client:
        r = await client.post("/v1/pipelines:validate", json={
            "steps": [
                {"op": "features", "provider": "colmap_cli"},
                {"op": "pairs"},
                {"op": "matches", "provider": "colmap_cli"},
                {"op": "verify"},
                {"op": "map", "provider": "colmap_cli"},
            ],
        })
        assert r.status_code == 200, r.text
        assert r.json() == {"valid": True, "errors": []}


async def test_executable_legacy_pipeline_validates_stage_params() -> None:
    async with await _client() as client:
        r = await client.post("/v1/pipelines:validate", json={
            "steps": [
                {"op": "features", "params": {"type": "bogus"}},
                {"op": "pairs"},
                {"op": "matches"},
                {"op": "verify"},
                {"op": "map"},
            ],
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["valid"] is False
        assert body["errors"][0]["reason"] == "invalid_attribute"
        assert body["errors"][0]["path"] == "steps.0.params.type"


async def test_native_processor_provider_selector_is_preflight_metadata() -> None:
    async with await _client() as client:
        r = await client.post("/v1/pipelines:validate", json={
            "steps": [{"processor": "features", "provider": "colmap_cli"}],
        })
        assert r.status_code == 200, r.text
        assert r.json() == {"valid": True, "errors": []}


async def test_provider_selector_is_reported_as_unsupported() -> None:
    async with await _client() as client:
        r = await client.post("/v1/pipelines:validate", json={
            "steps": [{"op": "features", "provider": "colmap_cli"}],
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["valid"] is False
        assert body["errors"][0]["reason"] == "provider_unsupported"
        assert body["errors"][0]["path"] == "steps.0.provider"


async def test_empty_provider_selector_is_rejected_as_body_error() -> None:
    async with await _client() as client:
        r = await client.post("/v1/pipelines:validate", json={
            "steps": [{"op": "features", "provider": ""}],
        })
        assert r.status_code == 422
        assert "at least 1 character" in r.json()["detail"]


async def test_inputs_ref_is_reserved() -> None:
    async with await _client() as client:
        r = await client.post("/v1/pipelines:validate", json={
            "steps": [{"ref": "inputs", "processor": "features"}],
        })
        assert r.status_code == 422
        assert "reserved" in r.json()["detail"]


async def test_null_attribute_value_is_invalid() -> None:
    async with await _client() as client:
        r = await client.post("/v1/pipelines:validate", json={
            "steps": [{"processor": "features", "attributes": {"type": None}}],
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["valid"] is False
        assert body["errors"][0]["reason"] == "invalid_attribute"
        assert body["errors"][0]["path"] == "steps.0.attributes.type"


async def test_unknown_attribute_is_reported_with_stable_reason() -> None:
    async with await _client() as client:
        r = await client.post("/v1/pipelines:validate", json={
            "steps": [{"processor": "features", "attributes": {"bogus": 1}}],
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["valid"] is False
        assert body["errors"][0]["reason"] == "unknown_attribute"
        assert body["errors"][0]["path"] == "steps.0.attributes.bogus"


async def test_empty_processor_ref_is_rejected() -> None:
    async with await _client() as client:
        r = await client.post("/v1/pipelines:validate", json={
            "steps": [{"ref": "", "processor": "features"}],
        })
        assert r.status_code == 422
        assert "body.steps.0" in r.json()["detail"]
