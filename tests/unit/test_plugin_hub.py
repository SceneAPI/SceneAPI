from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError as PydanticValidationError

from sfm_hub.discovery import discover_plugins, load_backend_entry_points
from sfm_hub.doctor import detect_external_tools, doctor_manifest
from sfm_hub.install import (
    build_container_service_install_plan,
    build_docker_install_plan,
    build_install_plan,
    parse_github_source,
)
from sfm_hub.models import (
    ContainerServiceBuild,
    ContainerServiceImage,
    ContainerServiceRuntime,
    DockerRuntime,
    PluginBackendCatalog,
    PluginManifest,
    TorchRuntime,
    UvRuntime,
)
from sfm_hub.provision import package_module_name
from sfm_hub.registry import get_manifest, list_manifests, search_manifests
from sfm_hub.routing import (
    ProviderAmbiguityError,
    ensure_provider_enabled,
    provider_records,
    resolve_provider,
)
from sfm_hub.state import (
    PluginState,
    RoutingProfile,
    load_state,
    record_manual_install,
    save_state,
    set_enabled,
    set_project_profile,
    set_provider_priority,
    upsert_profile,
)
from sfmapi.server.core.errors import ValidationError
from sfmapi.server.services import plugin_service, sfm_stage_service

pytestmark = pytest.mark.unit


def test_bundled_manifests_validate_and_include_initial_entries() -> None:
    manifests = list_manifests()
    plugin_ids = {manifest.plugin_id for manifest in manifests}

    assert {
        "colmap_cli",
        "pycolmap",
        "colmap_native",
        "realityscan_cli",
        "hloc",
        "instantsfm",
        "spheresfm",
        "gsplat",
        "brush",
        "lfs",
        "spirulae",
        "fastergs",
    } <= plugin_ids
    assert "gaussian_splatting_cuda" not in plugin_ids

    for manifest in manifests:
        assert manifest.github_url.startswith("https://github.com/SFMAPI/")
        assert manifest.entry_points
        assert manifest.providers
        assert manifest.runtime_mode_names()
        assert set(manifest.provider_ids()) == {
            provider.provider_id for provider in manifest.providers
        }


def test_torch_backed_plugins_declare_explicit_torch_runtime() -> None:
    expected_policy = {
        "fastergs": "required",
        "gsplat": "required",
        "hloc": "recommended",
        "instantsfm": "required",
        "spirulae": "required",
        "vismatch": "recommended",
    }
    for plugin_id, policy in expected_policy.items():
        torch_runtime = get_manifest(plugin_id).compatibility.torch

        assert torch_runtime is not None
        assert torch_runtime.policy == policy
        assert torch_runtime.device == "cuda"
        assert torch_runtime.cpu_index_url == "https://download.pytorch.org/whl/cpu"
        assert torch_runtime.install_env["TORCH_DEVICE"] == "cuda"
        if plugin_id == "gsplat":
            assert torch_runtime.index_url == "https://download.pytorch.org/whl/cu128"
            assert torch_runtime.packages == ["torch"]
            assert torch_runtime.install_env["GSPLAT_PACKAGE"] == "gsplat==1.5.3"
            assert torch_runtime.install_env["TORCH_CUDA_ARCH_LIST"] == "12.0"
        else:
            assert torch_runtime.index_url == "https://download.pytorch.org/whl/cu128"
            assert torch_runtime.packages == ["torch", "torchvision", "torchaudio"]
            assert torch_runtime.install_env["TORCH_PACKAGES"] == "torch torchvision torchaudio"

    instantsfm_runtime = get_manifest("instantsfm").runtime_modes.container_service
    assert instantsfm_runtime is not None
    assert instantsfm_runtime.execution.gpu == "required"
    assert instantsfm_runtime.image is not None
    assert instantsfm_runtime.image.build is not None
    assert instantsfm_runtime.image.build.args["TORCH_DEVICE"] == "cuda"


def test_3dgs_plugins_declare_container_service_radiance_contracts() -> None:
    expected = {
        "gsplat",
        "brush",
        "lfs",
        "spirulae",
        "fastergs",
    }

    for plugin_id in expected:
        manifest = get_manifest(plugin_id)
        runtime = manifest.runtime_modes.container_service

        assert runtime is not None
        assert manifest.runtime_modes.docker is not None
        assert "radiance.train" in manifest.capabilities
        assert "radiance.train" in manifest.providers[0].capabilities
        assert runtime.protocol == "sfmapi-plugin-http-v1"
        assert runtime.execution.path == "/execute"
        assert runtime.object_store is not None
        assert runtime.object_store.input_prefix == f"{plugin_id}/input/"
        assert runtime.object_store.output_prefix == f"{plugin_id}/output/"

    assert get_manifest("brush").compatibility.torch is None
    assert get_manifest("lfs").compatibility.torch is None
    assert get_manifest("gsplat").runtime_modes.container_service.execution.gpu == "required"
    assert get_manifest("spirulae").runtime_modes.container_service.execution.gpu == "required"
    assert get_manifest("fastergs").runtime_modes.container_service.execution.gpu == "required"


def test_package_module_name_strips_extras_for_provisioning() -> None:
    assert package_module_name("sfmapi-vismatch[engine]") == "sfmapi_vismatch"


def test_qualified_backend_registry_lookup_does_not_fallback_to_bare() -> None:
    from sfmapi.server.adapters.registry import get_backend, register_backend_provider
    from sfmapi.server.adapters.stub_backend import StubBackend

    def factory() -> StubBackend:
        return StubBackend()

    register_backend_provider("exact_missing_test", factory)

    with pytest.raises(KeyError, match="exact_missing_test@missing"):
        get_backend(provider="exact_missing_test@missing")


def test_schema_file_lists_required_manifest_fields() -> None:
    schema = json.loads(Path("sfm_hub/schemas/backend-plugin.schema.json").read_text())

    assert "schema_version" in schema["properties"]
    assert "schema_version" not in schema["required"]
    assert "plugin_id" in schema["required"]
    assert "github_url" in schema["required"]
    assert "entry_points" in schema["required"]
    assert "providers" in schema["required"]
    assert "runtime_modes" in schema["required"]
    assert "container_service" in schema["properties"]["runtime_modes"]["properties"]
    assert "datatypes" in schema["properties"]
    assert "processors" in schema["properties"]
    assert "pipelines" in schema["properties"]
    assert "processor_extensions" in schema["properties"]
    assert "plugin_dependencies" in schema["properties"]
    assert "plugin_dependency_manifest" in schema["$defs"]
    pipeline_schema = schema["$defs"]["plugin_pipeline"]
    assert pipeline_schema["properties"]["initial_inputs"]["uniqueItems"] is True


def test_plugin_manifest_defaults_schema_version_for_v1_compatibility() -> None:
    manifest = _typed_extension_manifest()
    manifest.pop("schema_version")

    assert PluginManifest.model_validate(manifest).schema_version == 1


def test_plugin_manifest_enforces_provider_selector_component_lengths() -> None:
    with pytest.raises(PydanticValidationError):
        PluginManifest.model_validate(_typed_extension_manifest(plugin_id="p" * 65))

    manifest = _typed_extension_manifest()
    providers = manifest["providers"]
    assert isinstance(providers, list)
    provider = providers[0]
    assert isinstance(provider, dict)
    provider["provider_id"] = "p" * 65

    with pytest.raises(PydanticValidationError):
        PluginManifest.model_validate(manifest)


def test_static_schema_enforces_provider_selector_component_lengths() -> None:
    schema = json.loads(Path("sfm_hub/schemas/backend-plugin.schema.json").read_text())

    assert schema["properties"]["plugin_id"]["maxLength"] == 64
    assert schema["$defs"]["provider"]["properties"]["provider_id"]["maxLength"] == 64


def test_plugin_manifest_rejects_private_package_name() -> None:
    schema = json.loads(Path("sfm_hub/schemas/backend-plugin.schema.json").read_text())
    manifest = _typed_extension_manifest(package_name="../secret")

    assert list(Draft202012Validator(schema).iter_errors(manifest))
    with pytest.raises(PydanticValidationError):
        PluginManifest.model_validate(manifest)


def test_pydantic_manifest_schema_exposes_runtime_id_constraints() -> None:
    schema = PluginManifest.model_json_schema()
    defs = schema["$defs"]

    assert schema["properties"]["plugin_id"]["maxLength"] == 64
    assert defs["ProviderManifest"]["properties"]["provider_id"]["maxLength"] == 64
    assert (
        defs["PluginDataTypeManifest"]["properties"]["type_id"]["pattern"] == r"^[a-z][a-z0-9_-]*$"
    )
    assert (
        defs["PluginProcessorManifest"]["properties"]["processor_id"]["pattern"]
        == r"^[a-z][a-z0-9_-]*$"
    )
    assert (
        defs["PluginPipelineManifest"]["properties"]["pipeline_id"]["pattern"]
        == r"^[a-z][a-z0-9_-]*$"
    )
    special_required = defs["PluginSpecialInputPortSpecManifest"]["properties"]["required"]
    assert special_required["const"] is False
    assert special_required["default"] is False
    special_attrs = defs["PluginProcessorExtensionManifest"]["properties"]["special_attributes"][
        "items"
    ]["$ref"].removeprefix("#/$defs/")
    assert special_attrs == "PluginSpecialAttributeManifest"
    special_attr_required = defs[special_attrs]["properties"]["required"]
    assert special_attr_required["const"] is False
    assert special_attr_required["default"] is False
    assert (
        defs["PluginProcessorExtensionManifest"]["properties"]["special_inputs"][
            "additionalProperties"
        ]
        is False
    )
    assert (
        "runtime PluginManifest validation"
        in (defs["PluginProcessorExtensionManifest"]["properties"]["special_inputs"]["description"])
    )
    assert (
        "runtime PluginManifest validation"
        in (
            defs["PluginProcessorExtensionManifest"]["properties"]["special_attributes"][
                "description"
            ]
        )
    )


def test_discovered_manifest_overrides_bundled_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sfm_hub.registry as registry

    bundled = get_manifest("hloc")
    discovered = bundled.model_copy(update={"display_name": "Installed HLOC"})

    monkeypatch.setattr(registry, "discovered_manifests", lambda: [discovered])

    assert registry.get_manifest("hloc").display_name == "Installed HLOC"


def _typed_extension_manifest(**overrides: object) -> dict[str, object]:
    manifest: dict[str, object] = {
        "schema_version": 1,
        "plugin_id": "typed_radiance",
        "display_name": "Typed Radiance",
        "description": "Typed extension manifest used by tests.",
        "package_name": "sfmapi-typed-radiance",
        "github_url": "https://github.com/SFMAPI/sfmapi_typed_radiance",
        "entry_points": ["sfmapi_typed_radiance:plugin"],
        "providers": [
            {
                "provider_id": "typed_radiance",
                "display_name": "Typed Radiance",
                "capabilities": ["radiance.train"],
            }
        ],
        "runtime_modes": {
            "container_service": {
                "protocol": "sfmapi-plugin-http-v1",
                "protocol_version": "1.0",
                "service": {"default_url": "http://127.0.0.1:8080"},
            }
        },
        "capabilities": ["radiance.train"],
        "backend_actions": [],
        "config_schemas": [],
        "artifact_contracts": [],
        "datatypes": [
            {
                "type_id": "radiance_field",
                "title": "Radiance field",
                "kind": "artifact",
                "description": "Learned radiance representation.",
            }
        ],
        "processors": [
            {
                "processor_id": "train",
                "title": "Radiance training",
                "consumer": {"model": {"datatype": "sparse_model"}},
                "supplier": {"field": {"datatype": "radiance_field"}},
                "attributes": [
                    {
                        "name": "method",
                        "type": "enum",
                        "enum": ["splat", "nerf"],
                        "default": "splat",
                    },
                    {"name": "max_steps", "type": "int", "min": 1},
                ],
                "capabilities": ["radiance.train"],
            }
        ],
        "pipelines": [
            {
                "pipeline_id": "radiance_from_sparse",
                "title": "Radiance from sparse model",
                "initial_inputs": ["sparse_model"],
                "steps": [{"ref": "train", "processor": "train"}],
            }
        ],
        "processor_extensions": [
            {
                "processor_id": "map",
                "special_inputs": {
                    "typed_radiance.prior": {
                        "datatype": "radiance_field",
                        "required": False,
                    }
                },
                "special_attributes": [{"name": "typed_radiance.radiance_weight", "type": "float"}],
            }
        ],
        "licenses": [],
        "upstream_projects": [],
        "compatibility": {},
        "conformance": {},
        "trust_tier": "community",
    }
    manifest.update(overrides)
    return manifest


def _enabled_plugin_state(*plugin_ids: str) -> PluginState:
    return PluginState(
        installed={
            plugin_id: {
                "plugin_id": plugin_id,
                "method": "test",
                "source_url": "",
                "ref": "test",
                "installed_at": "2026-01-01T00:00:00+00:00",
                "enabled": True,
            }
            for plugin_id in plugin_ids
        }
    )


def test_manifest_accepts_typed_extension_declarations() -> None:
    manifest = PluginManifest.model_validate(_typed_extension_manifest())

    assert manifest.datatypes[0].type_id == "radiance_field"
    assert manifest.processors[0].processor_id == "train"
    assert manifest.processors[0].attributes[0].enum == ["splat", "nerf"]
    assert manifest.pipelines[0].steps[0].processor == "train"
    assert manifest.processor_extensions[0].processor_id == "map"


def test_unique_plugin_local_processor_alias_is_public() -> None:
    from sfmapi.server.services import dataflow_registry_service

    manifest = PluginManifest.model_validate(_typed_extension_manifest())
    registry = dataflow_registry_service.effective_registry(
        state=_enabled_plugin_state("typed_radiance"),
        manifests=[manifest],
    )

    processor = registry.processor_for("train")

    assert processor is not None
    assert processor.processor_id == "typed_radiance.train"
    assert "train" in processor.aliases
    contract = processor.contract_dict()
    assert "train" in contract["aliases"]


def test_ambiguous_plugin_local_processor_alias_is_not_public() -> None:
    from sfmapi.server.services import dataflow_registry_service

    first = PluginManifest.model_validate(_typed_extension_manifest())
    second = PluginManifest.model_validate(
        _typed_extension_manifest(
            plugin_id="other_typed",
            package_name="sfmapi-other-typed",
            github_url="https://github.com/SFMAPI/sfmapi_other_typed",
            providers=[
                {
                    "provider_id": "other_typed",
                    "display_name": "Other Typed",
                    "capabilities": ["radiance.train"],
                }
            ],
            processor_extensions=[],
            pipelines=[],
        )
    )
    registry = dataflow_registry_service.effective_registry(
        state=_enabled_plugin_state("typed_radiance", "other_typed"),
        manifests=[first, second],
    )

    assert registry.processor_for("train") is None
    assert registry.processor_for("typed_radiance.train") is not None
    processor = registry.processor_for("typed_radiance.train")
    assert processor is not None
    assert "train" not in processor.aliases
    assert registry.processor_for("other_typed.train") is not None


def test_typed_extension_schema_accepts_manifest_shapes() -> None:
    manifest = _typed_extension_manifest()

    assert _schema_errors("plugin_datatype", manifest["datatypes"][0]) == []
    assert _schema_errors("plugin_processor", manifest["processors"][0]) == []
    assert _schema_errors("plugin_pipeline", manifest["pipelines"][0]) == []
    assert (
        _schema_errors(
            "plugin_processor_extension",
            manifest["processor_extensions"][0],
        )
        == []
    )


def test_manifest_rejects_processor_unknown_datatype() -> None:
    manifest = _typed_extension_manifest(
        processors=[
            {
                "processor_id": "bad_processor",
                "title": "Bad processor",
                "consumer": {"input": {"datatype": "missing_type"}},
                "supplier": {"output": {"datatype": "radiance_field"}},
                "capabilities": ["radiance.train"],
            }
        ],
        pipelines=[
            {
                "pipeline_id": "bad_pipeline",
                "title": "Bad pipeline",
                "initial_inputs": ["sparse_model"],
                "steps": [{"ref": "bad", "processor": "bad_processor"}],
            }
        ],
        processor_extensions=[],
    )

    with pytest.raises(PydanticValidationError, match="unknown datatype"):
        PluginManifest.model_validate(manifest)


def test_manifest_rejects_unknown_processor_extension_target() -> None:
    manifest = _typed_extension_manifest(
        processor_extensions=[
            {
                "processor_id": "missing.processor",
                "special_inputs": {
                    "typed_radiance.prior": {
                        "datatype": "radiance_field",
                        "required": False,
                    }
                },
            }
        ]
    )

    with pytest.raises(PydanticValidationError, match="unknown processor"):
        PluginManifest.model_validate(manifest)


def test_manifest_rejects_core_datatype_shadow() -> None:
    manifest = _typed_extension_manifest(
        datatypes=[
            {
                "type_id": "sparse_model",
                "title": "Shadowed sparse model",
                "kind": "artifact",
            }
        ],
        processors=[],
        pipelines=[],
        processor_extensions=[],
    )

    with pytest.raises(PydanticValidationError, match="cannot redefine core datatypes"):
        PluginManifest.model_validate(manifest)


def test_manifest_rejects_core_pipeline_shadow() -> None:
    manifest = _typed_extension_manifest()
    pipeline = manifest["pipelines"][0]  # type: ignore[index]
    assert isinstance(pipeline, dict)
    pipeline["pipeline_id"] = "sfm"

    with pytest.raises(PydanticValidationError, match="cannot redefine core pipelines"):
        PluginManifest.model_validate(manifest)


def test_manifest_pipeline_accepts_explicit_wire_refs() -> None:
    manifest = _typed_extension_manifest(
        pipelines=[
            {
                "pipeline_id": "merge_radiance",
                "title": "Merge radiance fields",
                "initial_inputs": ["sparse_model"],
                "steps": [
                    {
                        "ref": "merge",
                        "processor": "train",
                        "wires": {"model": "inputs.sparse_model"},
                    }
                ],
            }
        ]
    )

    parsed = PluginManifest.model_validate(manifest)

    assert parsed.pipelines[0].steps[0].wires["model"] == "inputs.sparse_model"


def test_manifest_pipeline_does_not_infer_omitted_optional_input() -> None:
    manifest = _typed_extension_manifest(
        processors=[
            {
                "processor_id": "train",
                "title": "Radiance training",
                "consumer": {"model": {"datatype": "sparse_model"}},
                "supplier": {"field": {"datatype": "radiance_field"}},
                "capabilities": ["radiance.train"],
            },
            {
                "processor_id": "consume",
                "title": "Consume optional radiance",
                "consumer": {
                    "model": {"datatype": "sparse_model"},
                    "prior": {"datatype": "radiance_field", "required": False},
                },
                "supplier": {"model": {"datatype": "sparse_model"}},
                "capabilities": ["radiance.train"],
            },
        ],
        pipelines=[
            {
                "pipeline_id": "optional_prior_omitted",
                "title": "Optional prior omitted",
                "initial_inputs": ["sparse_model", "radiance_field"],
                "steps": [
                    {"ref": "train", "processor": "train"},
                    {"ref": "consume", "processor": "consume"},
                ],
            }
        ],
        processor_extensions=[],
    )

    parsed = PluginManifest.model_validate(manifest)

    assert parsed.pipelines[0].pipeline_id == "optional_prior_omitted"


def test_manifest_pipeline_rejects_unverified_match_graph_for_mapping() -> None:
    manifest = _typed_extension_manifest(
        pipelines=[
            {
                "pipeline_id": "raw_matches_to_map",
                "title": "Invalid raw matches map",
                "initial_inputs": ["image_sequence"],
                "steps": [
                    {"ref": "extract", "processor": "features"},
                    {
                        "ref": "pairs",
                        "processor": "pairs",
                        "wires": {"features": "extract.features"},
                    },
                    {
                        "ref": "matches",
                        "processor": "matches",
                        "wires": {
                            "features": "extract.features",
                            "pairs": "pairs.pairs",
                        },
                    },
                    {
                        "ref": "map",
                        "processor": "map",
                        "wires": {
                            "features": "extract.features",
                            "matches": "matches.matches",
                        },
                    },
                ],
            }
        ]
    )

    with pytest.raises(PydanticValidationError, match="requires verified match_graph"):
        PluginManifest.model_validate(manifest)


def test_manifest_rejects_required_special_input() -> None:
    manifest = _typed_extension_manifest(
        processor_extensions=[
            {
                "processor_id": "map",
                "special_inputs": {
                    "typed_radiance.prior": {
                        "datatype": "radiance_field",
                        "required": True,
                    }
                },
            }
        ]
    )

    with pytest.raises(PydanticValidationError):
        PluginManifest.model_validate(manifest)


def test_manifest_rejects_required_special_attribute() -> None:
    manifest = _typed_extension_manifest(
        processor_extensions=[
            {
                "processor_id": "map",
                "special_attributes": [
                    {
                        "name": "typed_radiance.required_weight",
                        "type": "float",
                        "required": True,
                    }
                ],
            }
        ]
    )

    with pytest.raises(PydanticValidationError):
        PluginManifest.model_validate(manifest)

    errors = _schema_errors(
        "plugin_processor_extension",
        manifest["processor_extensions"][0],
    )
    assert errors


def test_attribute_schema_rejects_null_defaults_like_pydantic() -> None:
    attr = {"name": "weight", "type": "float", "default": None}

    with pytest.raises(PydanticValidationError, match="default cannot be null"):
        PluginManifest.model_validate(
            _typed_extension_manifest(
                processors=[
                    {
                        "processor_id": "train",
                        "title": "Radiance training",
                        "consumer": {"model": {"datatype": "sparse_model"}},
                        "supplier": {"field": {"datatype": "radiance_field"}},
                        "attributes": [attr],
                        "capabilities": ["radiance.train"],
                    }
                ]
            )
        )

    assert _schema_errors("plugin_attribute", attr)


def test_manifest_rejects_required_defaulted_attribute() -> None:
    manifest = _typed_extension_manifest(
        processors=[
            {
                "processor_id": "train",
                "title": "Radiance training",
                "consumer": {"model": {"datatype": "sparse_model"}},
                "supplier": {"field": {"datatype": "radiance_field"}},
                "attributes": [
                    {
                        "name": "max_steps",
                        "type": "int",
                        "required": True,
                        "default": 10,
                    }
                ],
                "capabilities": ["radiance.train"],
            }
        ],
        pipelines=[
            {
                "pipeline_id": "radiance_from_sparse",
                "title": "Radiance from sparse model",
                "initial_inputs": ["sparse_model"],
                "steps": [{"ref": "train", "processor": "train"}],
            }
        ],
    )

    with pytest.raises(PydanticValidationError, match="required and defaulted"):
        PluginManifest.model_validate(manifest)


def test_manifest_rejects_required_attribute_with_explicit_null_default() -> None:
    manifest = _typed_extension_manifest(
        processors=[
            {
                "processor_id": "train",
                "title": "Radiance training",
                "consumer": {"model": {"datatype": "sparse_model"}},
                "supplier": {"field": {"datatype": "radiance_field"}},
                "attributes": [
                    {
                        "name": "max_steps",
                        "type": "int",
                        "required": True,
                        "default": None,
                    }
                ],
                "capabilities": ["radiance.train"],
            }
        ],
    )

    with pytest.raises(PydanticValidationError, match="required and defaulted"):
        PluginManifest.model_validate(manifest)


def test_manifest_rejects_empty_providers_like_schema() -> None:
    manifest = _typed_extension_manifest(providers=[])

    with pytest.raises(PydanticValidationError, match="at least 1 item"):
        PluginManifest.model_validate(manifest)


def test_manifest_rejects_unqualified_special_extension_names() -> None:
    manifest = _typed_extension_manifest(
        processor_extensions=[
            {
                "processor_id": "map",
                "special_inputs": {"prior": {"datatype": "radiance_field", "required": False}},
                "special_attributes": [{"name": "radiance_weight", "type": "float"}],
            }
        ]
    )

    with pytest.raises(PydanticValidationError):
        PluginManifest.model_validate(manifest)


def test_manifest_rejects_extension_names_outside_plugin_namespace() -> None:
    manifest = _typed_extension_manifest(
        processor_extensions=[
            {
                "processor_id": "map",
                "special_inputs": {
                    "other_plugin.prior": {
                        "datatype": "radiance_field",
                        "required": False,
                    }
                },
                "special_attributes": [{"name": "other_plugin.radiance_weight", "type": "float"}],
            }
        ]
    )

    with pytest.raises(PydanticValidationError, match="owning plugin namespace"):
        PluginManifest.model_validate(manifest)


def test_manifest_rejects_trailing_dot_special_attribute_names() -> None:
    manifest = _typed_extension_manifest(
        processor_extensions=[
            {
                "processor_id": "map",
                "special_attributes": [
                    {"name": "typed_radiance.", "type": "float"},
                    {"name": "typed_radiance.weight.", "type": "float"},
                ],
            }
        ]
    )

    with pytest.raises(PydanticValidationError):
        PluginManifest.model_validate(manifest)


def test_static_schema_rejects_empty_segment_special_attribute_name() -> None:
    manifest = _typed_extension_manifest(
        processor_extensions=[
            {
                "processor_id": "map",
                "special_attributes": [{"name": "typed_radiance..weight", "type": "float"}],
            }
        ]
    )

    with pytest.raises(PydanticValidationError):
        PluginManifest.model_validate(manifest)

    assert _schema_errors(
        "plugin_processor_extension",
        manifest["processor_extensions"][0],
    )


def test_static_schema_allows_special_input_required_default() -> None:
    manifest = _typed_extension_manifest(
        plugin_id="typed-radiance",
        package_name="sfmapi-typed-radiance",
        github_url="https://github.com/SFMAPI/sfmapi_typed_radiance",
        entry_points=["sfmapi_typed_radiance:plugin"],
        processor_extensions=[
            {
                "processor_id": "map",
                "special_inputs": {"typed-radiance.prior": {"datatype": "radiance_field"}},
            }
        ],
    )

    parsed = PluginManifest.model_validate(manifest)
    assert parsed.processor_extensions[0].special_inputs["typed-radiance.prior"].required is False
    assert not _schema_errors(
        "plugin_processor_extension",
        manifest["processor_extensions"][0],
    )


def test_manifest_accepts_hyphenated_plugin_namespace_for_special_attributes() -> None:
    manifest = _typed_extension_manifest(
        plugin_id="typed-radiance",
        package_name="sfmapi-typed-radiance",
        github_url="https://github.com/SFMAPI/sfmapi_typed_radiance",
        entry_points=["sfmapi_typed_radiance:plugin"],
        processor_extensions=[
            {
                "processor_id": "map",
                "special_inputs": {
                    "typed-radiance.prior": {
                        "datatype": "radiance_field",
                        "required": False,
                    }
                },
                "special_attributes": [{"name": "typed-radiance.radiance_weight", "type": "float"}],
            }
        ],
    )

    parsed = PluginManifest.model_validate(manifest)

    assert parsed.plugin_id == "typed-radiance"
    assert (
        parsed.processor_extensions[0].special_attributes[0].name
        == "typed-radiance.radiance_weight"
    )


def test_manifest_rejects_malformed_special_input_names() -> None:
    manifest = _typed_extension_manifest(
        processor_extensions=[
            {
                "processor_id": "map",
                "special_inputs": {
                    "typed..radiance.prior": {
                        "datatype": "radiance_field",
                        "required": False,
                    }
                },
            }
        ],
    )

    with pytest.raises(PydanticValidationError):
        PluginManifest.model_validate(manifest)


def test_manifest_accepts_hyphenated_special_input_terminal_name() -> None:
    manifest = _typed_extension_manifest(
        plugin_id="typed-radiance",
        package_name="sfmapi-typed-radiance",
        github_url="https://github.com/SFMAPI/sfmapi_typed_radiance",
        entry_points=["sfmapi_typed_radiance:plugin"],
        processor_extensions=[
            {
                "processor_id": "map",
                "special_inputs": {
                    "typed-radiance.pose-prior": {
                        "datatype": "radiance_field",
                        "required": False,
                    }
                },
            }
        ],
    )

    parsed = PluginManifest.model_validate(manifest)

    assert "typed-radiance.pose-prior" in parsed.processor_extensions[0].special_inputs


def test_manifest_rejects_processor_capability_not_declared_by_provider() -> None:
    manifest = _typed_extension_manifest(
        providers=[
            {
                "provider_id": "typed_radiance",
                "display_name": "Typed Radiance",
                "capabilities": [],
            }
        ],
        capabilities=["radiance.train"],
    )

    with pytest.raises(PydanticValidationError, match="declared together by any provider"):
        PluginManifest.model_validate(manifest)


def test_manifest_rejects_processor_capabilities_split_across_providers() -> None:
    manifest = _typed_extension_manifest(
        providers=[
            {
                "provider_id": "typed_radiance_a",
                "display_name": "Typed Radiance A",
                "capabilities": ["radiance.train"],
            },
            {
                "provider_id": "typed_radiance_b",
                "display_name": "Typed Radiance B",
                "capabilities": ["radiance.evaluate"],
            },
        ],
        capabilities=["radiance.train", "radiance.evaluate"],
        processors=[
            {
                "processor_id": "full_workflow",
                "title": "Radiance full workflow",
                "consumer": {"model": {"datatype": "sparse_model"}},
                "supplier": {"field": {"datatype": "radiance_field"}},
                "capabilities": ["radiance.train", "radiance.evaluate"],
            }
        ],
        pipelines=[
            {
                "pipeline_id": "radiance_from_sparse",
                "title": "Radiance from sparse model",
                "initial_inputs": ["sparse_model"],
                "steps": [{"ref": "train", "processor": "full_workflow"}],
            }
        ],
        processor_extensions=[],
    )

    with pytest.raises(PydanticValidationError, match="declared together by any provider"):
        PluginManifest.model_validate(manifest)


@pytest.mark.parametrize(
    ("attribute", "message"),
    [
        ({"name": "max_steps", "type": "int", "default": "many"}, "default must match"),
        ({"name": "method", "type": "str", "enum": ["splat"]}, "uses enum values"),
        ({"name": "method", "type": "enum", "enum": ["splat", 3]}, "enum"),
        (
            {"name": "method", "type": "enum", "enum": ["splat"], "default": "nerf"},
            "default must match",
        ),
        ({"name": "weight", "type": "float", "min": 2, "max": 1}, "min must be <= max"),
        ({"name": "kind", "type": "datatype-ref", "default": "missing_type"}, "default must match"),
    ],
)
def test_manifest_rejects_invalid_attribute_schema(
    attribute: dict[str, object],
    message: str,
) -> None:
    manifest = _typed_extension_manifest(
        processors=[
            {
                "processor_id": "train",
                "title": "Radiance training",
                "consumer": {"model": {"datatype": "sparse_model"}},
                "supplier": {"field": {"datatype": "radiance_field"}},
                "attributes": [attribute],
                "capabilities": ["radiance.train"],
            }
        ]
    )

    with pytest.raises(PydanticValidationError, match=message):
        PluginManifest.model_validate(manifest)


def test_manifest_pipeline_can_use_declared_special_inputs_and_attributes() -> None:
    manifest = _typed_extension_manifest(
        pipelines=[
            {
                "pipeline_id": "radiance_prior_map",
                "title": "Radiance prior map",
                "initial_inputs": ["image_sequence", "radiance_field"],
                "steps": [
                    {"ref": "features", "processor": "features"},
                    {"ref": "pairs", "processor": "pairs"},
                    {"ref": "matches", "processor": "matches"},
                    {"ref": "verify", "processor": "verify"},
                    {
                        "ref": "map",
                        "processor": "map",
                        "attributes": {"typed_radiance.radiance_weight": 0.5},
                        "wires": {
                            "features": "features.features",
                            "matches": "verify.matches",
                            "typed_radiance.prior": "inputs.radiance_field",
                        },
                    },
                ],
            }
        ]
    )

    parsed = PluginManifest.model_validate(manifest)

    assert parsed.pipelines[0].steps[-1].wires["typed_radiance.prior"] == "inputs.radiance_field"


def test_manifest_pipeline_rejects_duplicate_explicit_fan_in() -> None:
    manifest = _typed_extension_manifest(
        processors=[
            {
                "processor_id": "field_merge",
                "title": "Merge radiance fields",
                "consumer": {
                    "fields": {
                        "datatype": "radiance_field",
                        "required": True,
                        "multiple": True,
                    }
                },
                "supplier": {"field": {"datatype": "radiance_field"}},
                "capabilities": ["radiance.train"],
            }
        ],
        pipelines=[
            {
                "pipeline_id": "bad_merge",
                "title": "Bad merge",
                "initial_inputs": ["radiance_field"],
                "steps": [
                    {
                        "ref": "merge",
                        "processor": "field_merge",
                        "wires": {
                            "fields": [
                                "inputs.radiance_field",
                                "inputs.radiance_field",
                            ]
                        },
                    }
                ],
            }
        ],
        processor_extensions=[],
    )

    with pytest.raises(PydanticValidationError, match="duplicate supplier reference"):
        PluginManifest.model_validate(manifest)

    pipeline = manifest["pipelines"][0]  # type: ignore[index]
    assert isinstance(pipeline, dict)
    errors = _schema_errors("plugin_pipeline", pipeline)
    assert errors


def test_backend_catalog_rejects_duplicate_inside_otherwise_valid_fan_in() -> None:
    manifest = _typed_extension_manifest(
        processors=[
            {
                "processor_id": "field_refine",
                "title": "Refine radiance field",
                "consumer": {"field": {"datatype": "radiance_field"}},
                "supplier": {"field": {"datatype": "radiance_field"}},
                "capabilities": ["radiance.train"],
            },
            {
                "processor_id": "field_merge",
                "title": "Merge radiance fields",
                "consumer": {
                    "fields": {
                        "datatype": "radiance_field",
                        "required": True,
                        "multiple": True,
                    }
                },
                "supplier": {"field": {"datatype": "radiance_field"}},
                "capabilities": ["radiance.train"],
            },
        ],
        pipelines=[
            {
                "pipeline_id": "bad_merge",
                "title": "Bad merge",
                "initial_inputs": ["radiance_field"],
                "steps": [
                    {"ref": "refine", "processor": "field_refine"},
                    {
                        "ref": "merge",
                        "processor": "field_merge",
                        "wires": {
                            "fields": [
                                "inputs.radiance_field",
                                "inputs.radiance_field",
                                "refine.field",
                            ]
                        },
                    },
                ],
            }
        ],
        processor_extensions=[],
    )

    with pytest.raises(PydanticValidationError, match="duplicate supplier reference"):
        PluginBackendCatalog.model_validate(
            {
                "plugin_id": "typed_radiance",
                "capabilities": ["radiance.train"],
                "datatypes": manifest["datatypes"],
                "processors": manifest["processors"],
                "pipelines": manifest["pipelines"],
                "processor_extensions": manifest["processor_extensions"],
            }
        )


def test_manifest_pipeline_validates_step_attributes() -> None:
    manifest = _typed_extension_manifest(
        pipelines=[
            {
                "pipeline_id": "radiance_from_sparse",
                "title": "Radiance from sparse model",
                "initial_inputs": ["sparse_model"],
                "steps": [
                    {
                        "ref": "train",
                        "processor": "train",
                        "attributes": {"method": "bogus"},
                    }
                ],
            }
        ]
    )

    with pytest.raises(PydanticValidationError, match="attribute 'method' must be enum"):
        PluginManifest.model_validate(manifest)


def test_manifest_pipeline_rejects_duplicate_initial_inputs() -> None:
    manifest = _typed_extension_manifest()
    pipeline = manifest["pipelines"][0]  # type: ignore[index]
    assert isinstance(pipeline, dict)
    pipeline["initial_inputs"] = ["sparse_model", "sparse_model"]

    with pytest.raises(PydanticValidationError, match="duplicate datatype"):
        PluginManifest.model_validate(manifest)

    errors = _schema_errors("plugin_pipeline", pipeline)
    assert any("non-unique" in message for message in errors)


def test_backend_catalog_validates_declarations_as_a_graph() -> None:
    manifest = _typed_extension_manifest()
    catalog = PluginBackendCatalog.model_validate(
        {
            "plugin_id": "typed_radiance",
            "capabilities": ["radiance.train"],
            "datatypes": manifest["datatypes"],
            "processors": manifest["processors"],
            "pipelines": manifest["pipelines"],
            "processor_extensions": manifest["processor_extensions"],
        }
    )

    assert catalog.processors[0].processor_id == "train"


def test_backend_catalog_requires_plugin_id_for_typed_declarations() -> None:
    manifest = _typed_extension_manifest()

    with pytest.raises(PydanticValidationError, match="plugin_id is required"):
        PluginBackendCatalog.model_validate(
            {
                "capabilities": ["radiance.train"],
                "datatypes": manifest["datatypes"],
                "processors": manifest["processors"],
                "processor_extensions": manifest["processor_extensions"],
            }
        )


def test_manifest_rejects_dotted_processor_declaration_ids() -> None:
    with pytest.raises(PydanticValidationError):
        PluginManifest.model_validate(
            _typed_extension_manifest(
                processors=[
                    {
                        "processor_id": "typed_radiance.train",
                        "title": "Owner-prefixed name",
                        "consumer": {"model": {"datatype": "sparse_model"}},
                        "supplier": {"field": {"datatype": "radiance_field"}},
                        "capabilities": ["radiance.train"],
                    }
                ]
            )
        )


def test_manifest_rejects_dotted_datatype_and_pipeline_declaration_ids() -> None:
    with pytest.raises(PydanticValidationError):
        PluginManifest.model_validate(
            _typed_extension_manifest(
                datatypes=[
                    {
                        "type_id": "typed_radiance.field",
                        "title": "Owner-prefixed field",
                        "kind": "artifact",
                    }
                ],
                processors=[],
                pipelines=[],
                processor_extensions=[],
            )
        )

    with pytest.raises(PydanticValidationError):
        PluginManifest.model_validate(
            _typed_extension_manifest(
                pipelines=[
                    {
                        "pipeline_id": "typed_radiance.pipeline",
                        "title": "Owner-prefixed pipeline",
                        "initial_inputs": ["sparse_model"],
                        "steps": [{"ref": "train", "processor": "train"}],
                    }
                ],
                processor_extensions=[],
            )
        )


def test_backend_catalog_rejects_dotted_declaration_ids() -> None:
    with pytest.raises(PydanticValidationError):
        PluginBackendCatalog.model_validate(
            {
                "plugin_id": "typed_radiance",
                "capabilities": ["radiance.train"],
                "processors": [
                    {
                        "processor_id": "typed_radiance.train",
                        "title": "Owner-prefixed name",
                        "consumer": {"model": {"datatype": "sparse_model"}},
                        "supplier": {"field": {"datatype": "sparse_model"}},
                        "capabilities": ["radiance.train"],
                    }
                ],
            }
        )


def test_static_schema_rejects_dotted_processor_declarations() -> None:
    errors = _schema_errors(
        "plugin_processor",
        {
            "processor_id": "other.train",
            "title": "Foreign-owned name",
            "consumer": {"model": {"datatype": "sparse_model"}},
            "supplier": {"field": {"datatype": "sparse_model"}},
            "capabilities": ["radiance.train"],
        },
    )

    assert any("does not match" in message for message in errors)


def test_static_schema_rejects_core_typed_id_shadows() -> None:
    assert _schema_errors(
        "plugin_datatype",
        {
            "type_id": "sparse_model",
            "title": "Shadowed sparse model",
            "kind": "artifact",
        },
    )
    assert _schema_errors(
        "plugin_processor",
        {
            "processor_id": "features",
            "title": "Shadowed features",
            "consumer": {"images": {"datatype": "image_sequence"}},
            "supplier": {"features": {"datatype": "feature_set"}},
            "capabilities": ["features.extract.sift"],
        },
    )
    assert _schema_errors(
        "plugin_pipeline",
        {
            "pipeline_id": "sfm",
            "title": "Shadowed SfM",
            "initial_inputs": ["image_sequence"],
            "steps": [{"ref": "extract", "processor": "features"}],
        },
    )


def test_manifest_accepts_plugin_owned_capability_vocabulary() -> None:
    manifest = PluginManifest.model_validate(
        _typed_extension_manifest(
            capabilities=["vendor.custom.train"],
            providers=[
                {
                    "provider_id": "typed_radiance",
                    "display_name": "Typed Radiance",
                    "capabilities": ["vendor.custom.train"],
                }
            ],
            processors=[
                {
                    "processor_id": "train",
                    "title": "Custom trainer",
                    "consumer": {"model": {"datatype": "sparse_model"}},
                    "supplier": {"field": {"datatype": "radiance_field"}},
                    "capabilities": ["vendor.custom.train"],
                },
            ],
        )
    )

    assert manifest.capabilities == ["vendor.custom.train"]
    assert manifest.processors[0].capabilities == ["vendor.custom.train"]


def test_manifest_rejects_invalid_capability_id_shape() -> None:
    with pytest.raises(PydanticValidationError, match="String should match pattern"):
        PluginManifest.model_validate(_typed_extension_manifest(capabilities=["Bad Capability"]))


@pytest.mark.parametrize(
    "github_url",
    [
        "https://user:pass@github.com/SFMAPI/sfmapi_typed_radiance",
        "https://github.com/SFMAPI/sfmapi_typed_radiance?token=secret",
        "https://github.com/SFMAPI/sfmapi_typed_radiance#sig=secret",
        "https://github.com/SFMAPI/sfmapi_typed_radiance%3Bsig=secret",
        "https://github.com/SFMAPI/sfmapi_typed_radiance/tree/main",
    ],
)
def test_manifest_rejects_private_github_urls(github_url: str) -> None:
    manifest = _typed_extension_manifest(github_url=github_url)

    with pytest.raises(PydanticValidationError):
        PluginManifest.model_validate(manifest)


@pytest.mark.parametrize(
    "url",
    [
        "https://user:pass@github.com/SFMAPI/sfmapi_typed_radiance",
        "https://github.com/SFMAPI/sfmapi_typed_radiance?token=secret",
        "https://github.com/SFMAPI/sfmapi_typed_radiance%3Bsig=secret",
    ],
)
def test_uv_runtime_rejects_private_source_urls(url: str) -> None:
    with pytest.raises(PydanticValidationError):
        UvRuntime.model_validate(
            {"source": "git", "url": url, "ref": "main", "package": "sfmapi-test"}
        )


def test_runtime_manifests_reject_private_build_inputs() -> None:
    for index_url in [
        "https://user:pass@download.pytorch.org/whl/cu128",
        "https://download.pytorch.org/whl/cu128?token=secret",
        "https://download.pytorch.org/whl/cu128%3Bsig=secret",
    ]:
        with pytest.raises(PydanticValidationError):
            TorchRuntime.model_validate({"index_url": index_url})

    for args in [
        {"API_TOKEN": "secret-value"},
        {"TORCH_INDEX_URL": "https://download.pytorch.org/whl/cu128?token=secret"},
        {"TORCH_INDEX_URL": "https://download.pytorch.org/whl/cu128%3Bsig=secret"},
        {"TORCH_INDEX_URL": "https%3A%2F%2Fexample.com%2Fwheel%3FX-Amz-Signature%3Dabc"},
        {"PIP_INDEX_URL": "https://pypi.org/simple"},
        {"UV_NO_SYNC": "1"},
        {"WHEEL_CACHE": "C:\\private\\wheels"},
        {"WHEEL_CACHE": "C%3A%5Cprivate%5Cwheels"},
    ]:
        with pytest.raises(PydanticValidationError):
            ContainerServiceBuild.model_validate(
                {
                    "source": "git",
                    "context": "https://github.com/SFMAPI/sfmapi_typed_radiance",
                    "ref": "main",
                    "args": args,
                }
            )

    for dockerfile in [
        "../secrets/Dockerfile",
        "C:/private/Dockerfile",
        r"C:\private\Dockerfile",
        "/tmp/Dockerfile",
        "Dockerfile?X-Amz-Signature=abc",
        "Dockerfile%3FX-Amz-Signature%3Dabc",
    ]:
        with pytest.raises(PydanticValidationError):
            ContainerServiceBuild.model_validate(
                {
                    "source": "git",
                    "context": "https://github.com/SFMAPI/sfmapi_typed_radiance",
                    "ref": "main",
                    "dockerfile": dockerfile,
                }
            )

    for build in [
        {"source": "local", "context": "C:/private/plugin"},
        {"source": "local", "context": "/tmp/private/plugin"},
        {"source": "local", "context": "plugin?X-Amz-Signature=abc"},
        {"source": "local", "ref": "main"},
        {"source": "release", "context": "https://artifacts.example/plugin.tar.gz?sig=abc"},
        {"source": "release", "context": "file:///tmp/plugin.tar.gz"},
        {"source": "release", "ref": "v1.0.0"},
    ]:
        with pytest.raises(PydanticValidationError):
            ContainerServiceBuild.model_validate(build)

    assert (
        ContainerServiceBuild.model_validate(
            {"source": "local", "context": "plugins/radiance"}
        ).context
        == "plugins/radiance"
    )

    for install_env in [
        {"TOKEN": "secret-value"},
        {"PIP_INDEX_URL": "https://pypi.org/simple"},
        {"TORCH_CACHE": "/home/me/private-cache"},
        {"TORCH_CACHE": "%2Fhome%2Fme%2Fprivate-cache"},
        {"TORCH_INDEX_URL": "https://download.pytorch.org/whl/cpu?token=secret"},
        {"TORCH_INDEX_URL": "https%3A%2F%2Fexample.com%2Ftorch%3Fsignature%3Dabc"},
    ]:
        with pytest.raises(PydanticValidationError):
            TorchRuntime.model_validate({"install_env": install_env})

    for build_context in [
        "https://user:pass@example.com/repo.git",
        "https://github.com/SFMAPI/sfmapi_private.git?token=secret",
        "file:///tmp/private-context",
    ]:
        with pytest.raises(PydanticValidationError):
            DockerRuntime.model_validate({"build_context": build_context})


def test_manifest_rejects_private_public_metadata_urls() -> None:
    for overrides in [
        {
            "licenses": [
                {"name": "Apache-2.0", "url": "https://licenses.example/apache?token=secret"}
            ]
        },
        {
            "upstream_projects": [
                {"name": "upstream", "url": "https://user:pass@example.com/project"}
            ]
        },
        {"conformance": {"report_url": "https://reports.example/run#sig=secret"}},
        {"compatibility": {"token": "secret-value"}},
        {"compatibility": {"path": "/home/me/private/model.bin"}},
        {"compatibility": {"runtime": {"cache_path": "C:\\private\\cache"}}},
        {"compatibility": {"runtime": {"cache_path": "C%3A%5Cprivate%5Ccache"}}},
        {"compatibility": {"artifact": "https%3A%2F%2Fs3.example%2Fb%3FX-Amz-Signature%3Dabc"}},
        {
            "runtime_modes": {
                "container_service": {
                    "protocol": "sfmapi-plugin-http-v1",
                    "protocol_version": "1.0",
                    "service": {"default_url": "http://plugin-hloc"},
                    "provenance": {"sbom_url": "https://sbom.example/report?X-Amz-Signature=abc"},
                }
            }
        },
    ]:
        with pytest.raises(PydanticValidationError):
            PluginManifest.model_validate(_typed_extension_manifest(**overrides))


def test_container_service_doctor_rejects_https_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = get_manifest("gsplat")
    runtime = manifest.runtime_modes.container_service
    assert runtime is not None
    assert runtime.service.url_env is not None
    monkeypatch.setenv(runtime.service.url_env, "https://plugin.example")

    report = doctor_manifest(manifest)
    check = next(item for item in report.checks if item.name == "container_service")

    assert check.status == "fail"
    assert check.metadata["reason"] == "invalid_endpoint"


def test_container_service_runtime_rejects_signed_paths() -> None:
    for runtime_fragment in [
        {"healthcheck": {"path": "/healthz?X-Amz-Signature=abc"}},
        {"healthcheck": {"path": "/healthz;sig=abc"}},
        {"healthcheck": {"path": "/healthz;GoogleAccessId=abc"}},
        {"healthcheck": {"path": "/healthz%253BX-Amz-Signature%253Dabc"}},
        {"healthcheck": {"path": "/healthz%253FX-Amz-Signature%253Dabc"}},
        {"execution": {"path": "/execute?token=abc"}},
        {"execution": {"path": "/execute;X-Amz-Signature=abc"}},
        {"execution": {"path": "/execute;sigv4=abc"}},
        {"execution": {"path": "/execute%253Bsig%253Dabc"}},
        {"execution": {"path": "/execute%253FGoogleAccessId%253Dabc"}},
    ]:
        with pytest.raises(PydanticValidationError):
            ContainerServiceRuntime.model_validate(
                {
                    "protocol": "sfmapi-plugin-http-v1",
                    "protocol_version": "1.0",
                    "service": {"default_url": "http://plugin-hloc"},
                    **runtime_fragment,
                }
            )


def test_backend_catalog_rejects_invalid_pipeline_attributes() -> None:
    with pytest.raises(PydanticValidationError, match="attribute 'type' must be enum"):
        PluginBackendCatalog.model_validate(
            {
                "plugin_id": "typed_radiance",
                "capabilities": ["features.extract.sift"],
                "pipelines": [
                    {
                        "pipeline_id": "bad_features",
                        "title": "Bad features",
                        "steps": [
                            {
                                "ref": "extract",
                                "processor": "features",
                                "attributes": {"type": "bogus"},
                            }
                        ],
                    }
                ],
            }
        )


def test_backend_catalog_rejects_unknown_datatype_refs() -> None:
    with pytest.raises(PydanticValidationError, match="unknown datatype"):
        PluginBackendCatalog.model_validate(
            {
                "plugin_id": "typed_radiance",
                "capabilities": ["radiance.train"],
                "processors": [
                    {
                        "processor_id": "train",
                        "title": "Radiance training",
                        "consumer": {"model": {"datatype": "missing_type"}},
                        "supplier": {"field": {"datatype": "sparse_model"}},
                        "capabilities": ["radiance.train"],
                    }
                ],
            }
        )


def _schema_errors(def_name: str, value: dict[str, object]) -> list[str]:
    schema = json.loads(Path("sfm_hub/schemas/backend-plugin.schema.json").read_text())
    validator = Draft202012Validator({"$ref": f"#/$defs/{def_name}", "$defs": schema["$defs"]})
    return [error.message for error in validator.iter_errors(value)]


@pytest.mark.parametrize(
    ("model", "def_name", "value"),
    [
        (
            UvRuntime,
            "uv_runtime",
            {
                "source": "git",
                "url": "https://github.com/SFMAPI/sfmapi_hloc",
                "package": "sfmapi-hloc",
            },
        ),
        (
            DockerRuntime,
            "docker_runtime",
            {"image": "ghcr.io/sfmapi/hloc-plugin:1.0"},
        ),
        (
            ContainerServiceRuntime,
            "container_service_runtime",
            {
                "protocol": "sfmapi-plugin-http-v1",
                "protocol_version": "1.0",
                "service": {"default_url": "http://plugin-hloc:8080"},
            },
        ),
        (
            ContainerServiceRuntime,
            "container_service_runtime",
            {
                "protocol": "sfmapi-plugin-http-v1",
                "protocol_version": "1.0",
                "service": {"default_url": "http://plugin-hloc:8080"},
                "image": {"image": "ghcr.io/sfmapi/hloc-plugin:1.0"},
            },
        ),
        (
            ContainerServiceRuntime,
            "container_service_runtime",
            {
                "protocol": "sfmapi-plugin-http-v1",
                "protocol_version": "1.0",
                "service": {"url_env": "SFMAPI_TEST_PLUGIN_URL"},
            },
        ),
    ],
)
def test_runtime_schema_and_pydantic_accept_same_valid_shapes(
    model: type[UvRuntime | DockerRuntime | ContainerServiceRuntime],
    def_name: str,
    value: dict[str, object],
) -> None:
    assert _schema_errors(def_name, value) == []
    model.model_validate(value)


@pytest.mark.parametrize(
    ("model", "def_name", "value"),
    [
        (
            UvRuntime,
            "uv_runtime",
            {
                "source": "git",
                "url": "https://github.com/SFMAPI/sfmapi_hloc",
                "package": "../secret",
            },
        ),
        (
            DockerRuntime,
            "docker_runtime",
            {"image": "localhost:5000/hloc:latest"},
        ),
        (
            ContainerServiceRuntime,
            "container_service_runtime",
            {
                "protocol": "sfmapi-plugin-http-v1",
                "protocol_version": "1.0",
                "service": {},
            },
        ),
        (
            ContainerServiceRuntime,
            "container_service_runtime",
            {
                "protocol": "sfmapi-plugin-http-v1",
                "protocol_version": "1.0",
                "service": {"default_url": "http://plugin-hloc:8080"},
                "image": {"image": "ghcr.io/sfmapi/hloc-plugin:latest?token=secret"},
            },
        ),
        (
            ContainerServiceRuntime,
            "container_service_runtime",
            {
                "protocol": "sfmapi-plugin-http-v1",
                "protocol_version": "1.0",
                "service": {"default_url": "http://"},
            },
        ),
        (
            ContainerServiceRuntime,
            "container_service_runtime",
            {
                "protocol": "sfmapi-plugin-http-v1",
                "protocol_version": "1.0",
                "service": {"url_env": "plugin_url"},
            },
        ),
    ],
)
def test_runtime_schema_and_pydantic_reject_same_invalid_shapes(
    model: type[UvRuntime | DockerRuntime | ContainerServiceRuntime],
    def_name: str,
    value: dict[str, object],
) -> None:
    assert _schema_errors(def_name, value)
    with pytest.raises(PydanticValidationError):
        model.model_validate(value)


def test_container_service_default_url_rejects_encoded_signed_params() -> None:
    with pytest.raises(PydanticValidationError):
        ContainerServiceRuntime.model_validate(
            {
                "protocol": "sfmapi-plugin-http-v1",
                "protocol_version": "1.0",
                "service": {
                    "default_url": ("http://plugin-hloc/health%253FX-Amz-Signature%253Dabc")
                },
            }
        )


@pytest.mark.parametrize(
    "image",
    [
        "127.0.0.1:5000/hloc:latest",
        "10.0.0.5:5000/hloc:latest",
        "registry.internal/hloc:latest",
        "host.docker.internal/hloc:latest",
    ],
)
def test_runtime_images_reject_private_registry_hosts(image: str) -> None:
    with pytest.raises(PydanticValidationError):
        DockerRuntime.model_validate({"image": image})
    with pytest.raises(PydanticValidationError):
        ContainerServiceImage.model_validate({"image": image})


@pytest.mark.parametrize(
    "attribute",
    [
        {"name": "count", "type": "int", "default": "many"},
        {"name": "enabled", "type": "bool", "default": "true"},
        {"name": "mode", "type": "str", "enum": ["fast"]},
        {"name": "label", "type": "str", "min": 1},
    ],
)
def test_attribute_schema_rejects_invalid_shapes_like_pydantic(
    attribute: dict[str, object],
) -> None:
    assert _schema_errors("plugin_attribute", attribute)
    with pytest.raises(PydanticValidationError):
        PluginManifest.model_validate(
            _typed_extension_manifest(
                processors=[
                    {
                        "processor_id": "train",
                        "title": "Radiance training",
                        "consumer": {"model": {"datatype": "sparse_model"}},
                        "supplier": {"field": {"datatype": "radiance_field"}},
                        "attributes": [attribute],
                        "capabilities": ["radiance.train"],
                    }
                ]
            )
        )


def test_registry_search_and_github_install_plan() -> None:
    assert [manifest.plugin_id for manifest in search_manifests("hloc")] == ["hloc"]

    source = parse_github_source(
        "https://github.com/SFMAPI/sfmapi_colmap_cli/tree/v1.2.3",
        package="sfmapi-colmap-cli",
    )
    plan = build_install_plan(source)

    assert source.normalized_url == "https://github.com/SFMAPI/sfmapi_colmap_cli.git"
    assert source.ref == "v1.2.3"
    assert plan.command == [
        "uv",
        "pip",
        "install",
        "sfmapi-colmap-cli @ git+https://github.com/SFMAPI/sfmapi_colmap_cli.git@v1.2.3",
    ]
    assert not plan.warnings


@pytest.mark.parametrize(
    ("source", "kwargs", "message"),
    [
        (
            "https://github.com/SFMAPI/sfmapi_hloc.git?token=secret",
            {},
            "query or fragment",
        ),
        (
            "https://github.com/SFMAPI/sfmapi_hloc.git#sig=secret",
            {},
            "query or fragment",
        ),
        (
            "https://user:pass@github.com/SFMAPI/sfmapi_hloc.git",
            {},
            "credentials",
        ),
        (
            "https://github.com/SFMAPI/sfmapi_hloc.git",
            {"ref": "token-branch"},
            "public branch",
        ),
        (
            "https://github.com/SFMAPI/sfmapi_hloc.git",
            {"package": "sfmapi-token"},
            "public Python package",
        ),
    ],
)
def test_github_install_source_rejects_private_inputs(
    source: str,
    kwargs: dict[str, str],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        parse_github_source(source, **kwargs)


def test_mutable_github_refs_warn() -> None:
    plan = build_install_plan(parse_github_source("SFMAPI/sfmapi_hloc"))

    assert plan.source.ref == "main"
    assert plan.warnings


def test_all_bundled_uv_plugins_plan_repo_install_and_provisioning() -> None:
    planned = []
    for manifest in list_manifests():
        if manifest.runtime_modes.uv is None:
            continue
        result = plugin_service.install_plugin(
            manifest.plugin_id,
            method="uv",
            dry_run=True,
            provision_runtime=True,
        )
        planned.append(manifest.plugin_id)

        assert result["installed"] is False
        assert result["command"][:3] == ["uv", "pip", "install"]
        assert result["direct_reference"].startswith(f"{manifest.package_name} @ git+")
        assert manifest.github_url in result["direct_reference"]
        assert result["provision_runtime"] is True
        assert result["provisioning"] is not None
        assert result["provisioning"]["steps"]

    assert planned


def test_docker_install_plan_reports_missing_image() -> None:
    source = parse_github_source("SFMAPI/sfmapi_colmap_cli", package="sfmapi-colmap-cli")
    plan = build_docker_install_plan(
        "colmap_cli", get_manifest("colmap_cli").runtime_modes.docker, source=source
    )

    assert plan.method == "docker"
    assert plan.warnings


def test_hloc_does_not_advertise_unimplemented_docker_runtime() -> None:
    manifest = get_manifest("hloc")
    source = parse_github_source(manifest.github_url, package=manifest.package_name)
    plan = build_docker_install_plan("hloc", manifest.runtime_modes.docker, source=source)

    assert manifest.runtime_modes.enabled_modes() == ["uv"]
    assert plan.method == "docker"
    assert plan.command == []
    assert plan.warnings == ["plugin 'hloc' does not define a docker runtime"]


def test_container_service_runtime_is_typed_and_plannable() -> None:
    runtime = ContainerServiceRuntime.model_validate(
        {
            "protocol": "sfmapi-plugin-http-v1",
            "protocol_version": "1.0",
            "service": {"default_url": "http://plugin-hloc:8080"},
            "healthcheck": {"path": "/healthz", "timeout_seconds": 5},
        }
    )
    source = parse_github_source("SFMAPI/sfmapi_hloc", package="sfmapi-hloc")

    plan = build_container_service_install_plan("hloc", runtime, source=source)

    assert plan.method == "container_service"
    assert plan.command == []
    assert plan.direct_reference == "container_service:http://plugin-hloc:8080"
    assert plan.warnings


def test_container_service_install_plan_provisions_declared_image() -> None:
    runtime = ContainerServiceRuntime.model_validate(
        {
            "protocol": "sfmapi-plugin-http-v1",
            "protocol_version": "1.0",
            "service": {"default_url": "http://127.0.0.1:8098"},
            "image": {"image": "ghcr.io/sfmapi/hloc-plugin:1.0"},
        }
    )
    source = parse_github_source("SFMAPI/sfmapi_hloc", package="sfmapi-hloc")

    plan = build_container_service_install_plan("hloc", runtime, source=source)

    assert plan.method == "container_service"
    assert plan.direct_reference == "ghcr.io/sfmapi/hloc-plugin:1.0"
    assert plan.command[1:] == ["-m", "sfm_hub.container_runtime", "provision", "hloc"]


def test_container_service_image_dry_run_is_public_attach_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = get_manifest("hloc").model_copy(deep=True)
    manifest.runtime_modes.container_service = ContainerServiceRuntime.model_validate(
        {
            "protocol": "sfmapi-plugin-http-v1",
            "protocol_version": "1.0",
            "service": {"default_url": "http://127.0.0.1:8098"},
            "image": {"image": "ghcr.io/sfmapi/hloc-plugin:1.0"},
        }
    )
    monkeypatch.setattr(plugin_service, "get_manifest", lambda plugin_id: manifest)

    result = plugin_service.install_plugin(
        "hloc",
        method="container_service",
        dry_run=True,
    )

    assert result["command"] == []
    assert result["direct_reference"] == "ghcr.io/sfmapi/hloc-plugin:1.0"
    assert result["warnings"] == [plugin_service.IMAGE_BACKED_CONTAINER_SERVICE_ATTACH_WARNING]
    assert result["provisioning_status"] == "not_requested"


def test_container_service_runtime_rejects_malformed_endpoint() -> None:
    with pytest.raises(PydanticValidationError, match="default_url or url_env"):
        ContainerServiceRuntime.model_validate(
            {
                "protocol": "sfmapi-plugin-http-v1",
                "protocol_version": "1.0",
                "service": {},
            }
        )

    with pytest.raises(PydanticValidationError, match="must include a host"):
        ContainerServiceRuntime.model_validate(
            {
                "protocol": "sfmapi-plugin-http-v1",
                "protocol_version": "1.0",
                "service": {"default_url": "http://"},
            }
        )
    for bad_url in [
        "https://user:pass@plugin-hloc",
        "http://plugin-hloc/path#fragment",
        "http://plugin-hloc?token=secret",
        "http://bad host",
    ]:
        with pytest.raises(PydanticValidationError):
            ContainerServiceRuntime.model_validate(
                {
                    "protocol": "sfmapi-plugin-http-v1",
                    "protocol_version": "1.0",
                    "service": {"default_url": bad_url},
                }
            )

    with pytest.raises(PydanticValidationError, match="url_env must match"):
        ContainerServiceRuntime.model_validate(
            {
                "protocol": "sfmapi-plugin-http-v1",
                "protocol_version": "1.0",
                "service": {"url_env": "plugin_url"},
            }
        )


def test_container_service_doctor_reports_unconfigured_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = get_manifest("hloc").model_copy(deep=True)
    manifest.runtime_modes.container_service = ContainerServiceRuntime.model_validate(
        {
            "protocol": "sfmapi-plugin-http-v1",
            "protocol_version": "1.0",
            "service": {"url_env": "SFMAPI_TEST_PLUGIN_URL"},
        }
    )
    monkeypatch.delenv("SFMAPI_TEST_PLUGIN_URL", raising=False)

    report = doctor_manifest(manifest)
    check = next(item for item in report.checks if item.name == "container_service")

    assert check.status == "warn"
    assert "SFMAPI_TEST_PLUGIN_URL" in check.detail


def test_container_service_doctor_rejects_private_env_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = get_manifest("hloc").model_copy(deep=True)
    manifest.runtime_modes.container_service = ContainerServiceRuntime.model_validate(
        {
            "protocol": "sfmapi-plugin-http-v1",
            "protocol_version": "1.0",
            "service": {"url_env": "SFMAPI_TEST_PLUGIN_URL"},
        }
    )
    monkeypatch.setenv(
        "SFMAPI_TEST_PLUGIN_URL",
        "http://user:pass@127.0.0.1:1?token=secret#sig=secret",
    )

    report = doctor_manifest(manifest)
    check = next(item for item in report.checks if item.name == "container_service")
    serialized = check.model_dump_json()

    assert check.status == "fail"
    assert check.detail == "container service endpoint from SFMAPI_TEST_PLUGIN_URL is invalid"
    assert check.metadata["reason"] == "credentialed_endpoint"
    assert "user:pass" not in serialized
    assert "token=secret" not in serialized
    assert "sig=secret" not in serialized


def _start_container_service(
    responses: dict[str, tuple[int, bytes]],
) -> tuple[ThreadingHTTPServer, Thread, str]:
    class HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            response = responses.get(self.path)
            if response is None:
                self.send_response(404)
                self.end_headers()
                return
            status, body = response
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), HealthHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, thread, f"http://{host}:{port}"


def _catalog_responses(plugin_id: str) -> dict[str, tuple[int, bytes]]:
    return {
        "/datatypes": (
            200,
            f'{{"schema_version":1,"plugin_id":"{plugin_id}","datatypes":[]}}'.encode(),
        ),
        "/processors": (
            200,
            (
                f'{{"schema_version":1,"plugin_id":"{plugin_id}",'
                '"processors":[],"processor_extensions":[]}'
            ).encode(),
        ),
        "/pipelines": (
            200,
            f'{{"schema_version":1,"plugin_id":"{plugin_id}","pipelines":[]}}'.encode(),
        ),
    }


def test_container_service_doctor_checks_health_endpoint() -> None:
    server, thread, base_url = _start_container_service(
        {
            "/healthz": (200, b'{"status":"ok"}'),
            "/version": (
                200,
                b'{"protocol":"sfmapi-plugin-http-v1","protocol_version":"1.0"}',
            ),
            **_catalog_responses("hloc"),
        }
    )
    try:
        manifest = get_manifest("hloc").model_copy(deep=True)
        manifest.runtime_modes.container_service = ContainerServiceRuntime.model_validate(
            {
                "protocol": "sfmapi-plugin-http-v1",
                "protocol_version": "1.0",
                "service": {"default_url": base_url},
            }
        )

        report = doctor_manifest(manifest)
        check = next(item for item in report.checks if item.name == "container_service")

        assert check.status == "pass"
        assert "sfmapi-plugin-http-v1 1.0" in check.detail
        assert check.metadata["protocol"] == "sfmapi-plugin-http-v1"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_container_service_doctor_accepts_legacy_service_without_catalog() -> None:
    server, thread, base_url = _start_container_service(
        {
            "/healthz": (200, b'{"status":"ok"}'),
            "/version": (
                200,
                b'{"protocol":"sfmapi-plugin-http-v1","protocol_version":"1.0"}',
            ),
        }
    )
    try:
        manifest = get_manifest("hloc").model_copy(deep=True)
        manifest.runtime_modes.container_service = ContainerServiceRuntime.model_validate(
            {
                "protocol": "sfmapi-plugin-http-v1",
                "protocol_version": "1.0",
                "service": {"default_url": base_url},
            }
        )

        report = doctor_manifest(manifest)
        check = next(item for item in report.checks if item.name == "container_service")

        assert check.status == "pass"
        assert "legacy extension catalog is absent" in check.detail
        assert check.metadata["catalog"] == "absent"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_container_service_doctor_requires_catalog_for_protocol_1_1() -> None:
    server, thread, base_url = _start_container_service(
        {
            "/healthz": (200, b'{"status":"ok"}'),
            "/version": (
                200,
                b'{"protocol":"sfmapi-plugin-http-v1","protocol_version":"1.1"}',
            ),
        }
    )
    try:
        manifest = get_manifest("hloc").model_copy(deep=True)
        manifest.runtime_modes.container_service = ContainerServiceRuntime.model_validate(
            {
                "protocol": "sfmapi-plugin-http-v1",
                "protocol_version": "1.0",
                "service": {"default_url": base_url},
            }
        )

        report = doctor_manifest(manifest)
        check = next(item for item in report.checks if item.name == "container_service")

        assert check.status == "fail"
        assert check.metadata["reason"] == "missing_catalog_endpoint"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_container_service_doctor_rejects_missing_catalog_arrays() -> None:
    server, thread, base_url = _start_container_service(
        {
            "/healthz": (200, b'{"status":"ok"}'),
            "/version": (
                200,
                b'{"protocol":"sfmapi-plugin-http-v1","protocol_version":"1.0"}',
            ),
            "/datatypes": (200, b'{"schema_version":1,"plugin_id":"hloc"}'),
            "/processors": (
                200,
                b'{"schema_version":1,"plugin_id":"hloc","processors":[],"processor_extensions":[]}',
            ),
            "/pipelines": (
                200,
                b'{"schema_version":1,"plugin_id":"hloc","pipelines":[]}',
            ),
        }
    )
    try:
        manifest = get_manifest("hloc").model_copy(deep=True)
        manifest.runtime_modes.container_service = ContainerServiceRuntime.model_validate(
            {
                "protocol": "sfmapi-plugin-http-v1",
                "protocol_version": "1.0",
                "service": {"default_url": base_url},
            }
        )

        report = doctor_manifest(manifest)
        check = next(item for item in report.checks if item.name == "container_service")

        assert check.status == "fail"
        assert check.metadata["reason"] == "bad_catalog_shape"
        assert "missing required field" in check.detail
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_container_service_doctor_bad_catalog_shape_hides_service_url() -> None:
    server, thread, base_url = _start_container_service(
        {
            "/healthz": (200, b'{"status":"ok"}'),
            "/version": (
                200,
                b'{"protocol":"sfmapi-plugin-http-v1","protocol_version":"1.0"}',
            ),
            "/datatypes": (200, b"[]"),
            "/processors": (
                200,
                b'{"schema_version":1,"plugin_id":"hloc","processors":[],"processor_extensions":[]}',
            ),
            "/pipelines": (
                200,
                b'{"schema_version":1,"plugin_id":"hloc","pipelines":[]}',
            ),
        }
    )
    try:
        manifest = get_manifest("hloc").model_copy(deep=True)
        manifest.runtime_modes.container_service = ContainerServiceRuntime.model_validate(
            {
                "protocol": "sfmapi-plugin-http-v1",
                "protocol_version": "1.0",
                "service": {"default_url": base_url},
            }
        )

        report = doctor_manifest(manifest)
        check = next(item for item in report.checks if item.name == "container_service")

        assert check.status == "fail"
        assert check.metadata["reason"] == "bad_catalog_shape"
        assert "datatypes catalog endpoint returned a non-object JSON value" in check.detail
        assert base_url not in check.detail
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.mark.parametrize(
    ("responses", "reason"),
    [
        (
            {"/version": (200, b'{"protocol":"sfmapi-plugin-http-v1","protocol_version":"1.0"}')},
            "http_error",
        ),
        ({"/healthz": (503, b'{"status":"down"}')}, "http_error"),
        ({"/healthz": (200, b'{"status":"ok"}')}, "version_http_error"),
        (
            {
                "/healthz": (200, b'{"status":"ok"}'),
                "/version": (200, b"not-json"),
            },
            "bad_version_json",
        ),
        (
            {
                "/healthz": (200, b'{"status":"ok"}'),
                "/version": (200, b'{"protocol":"other","protocol_version":"1.0"}'),
            },
            "protocol_mismatch",
        ),
        (
            {
                "/healthz": (200, b'{"status":"ok"}'),
                "/version": (
                    200,
                    b'{"protocol":"sfmapi-plugin-http-v1","protocol_version":"2.0"}',
                ),
            },
            "protocol_version_mismatch",
        ),
    ],
)
def test_container_service_doctor_rejects_bad_protocol_health(
    responses: dict[str, tuple[int, bytes]],
    reason: str,
) -> None:
    server, thread, base_url = _start_container_service(responses)
    try:
        manifest = get_manifest("hloc").model_copy(deep=True)
        manifest.runtime_modes.container_service = ContainerServiceRuntime.model_validate(
            {
                "protocol": "sfmapi-plugin-http-v1",
                "protocol_version": "1.0",
                "service": {"default_url": base_url},
            }
        )

        report = doctor_manifest(manifest)
        check = next(item for item in report.checks if item.name == "container_service")

        assert check.status == "fail"
        assert check.metadata["reason"] == reason
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_container_service_install_plan_reports_missing_runtime() -> None:
    manifest = get_manifest("hloc")
    source = parse_github_source(manifest.github_url, package=manifest.package_name)
    result = plugin_service.install_plugin(
        "hloc",
        method="container_service",
        dry_run=True,
    )

    assert manifest.runtime_modes.container_service is None
    assert build_container_service_install_plan(
        "hloc",
        manifest.runtime_modes.container_service,
        source=source,
    ).warnings == ["plugin 'hloc' does not define a container_service runtime"]
    assert result["method"] == "container_service"
    assert result["command"] == []
    assert result["warnings"] == ["plugin 'hloc' does not define a container_service runtime"]


def test_container_service_install_rejects_missing_runtime_execution() -> None:
    with pytest.raises(ValidationError, match="does not define a container_service runtime"):
        plugin_service.install_plugin(
            "hloc",
            method="container_service",
            dry_run=False,
            allow_unsafe_execution=True,
        )


def test_container_service_install_dry_run_does_not_contact_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = get_manifest("hloc").model_copy(deep=True)
    manifest.runtime_modes.container_service = ContainerServiceRuntime.model_validate(
        {
            "protocol": "sfmapi-plugin-http-v1",
            "protocol_version": "1.0",
            "service": {"default_url": "http://127.0.0.1:9"},
        }
    )
    monkeypatch.setattr(plugin_service, "get_manifest", lambda plugin_id: manifest)

    result = plugin_service.install_plugin(
        "hloc",
        method="container_service",
        dry_run=True,
    )

    assert result["installed"] is False
    assert result["direct_reference"] == "container_service:http://127.0.0.1:9"


def test_container_service_install_requires_healthy_protocol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server, thread, base_url = _start_container_service(
        {
            "/healthz": (200, b'{"status":"ok"}'),
            "/version": (
                200,
                b'{"protocol":"sfmapi-plugin-http-v1","protocol_version":"2.0"}',
            ),
        }
    )
    try:
        manifest = get_manifest("hloc").model_copy(deep=True)
        manifest.runtime_modes.container_service = ContainerServiceRuntime.model_validate(
            {
                "protocol": "sfmapi-plugin-http-v1",
                "protocol_version": "1.0",
                "service": {"default_url": base_url},
            }
        )
        monkeypatch.setattr(plugin_service, "get_manifest", lambda plugin_id: manifest)

        with pytest.raises(ValidationError, match="protocol version mismatch"):
            plugin_service.install_plugin(
                "hloc",
                method="container_service",
                dry_run=False,
                allow_unsafe_execution=True,
                request_id="550e8400-e29b-41d4-a716-446655440042",
            )

        record = load_state().installed["hloc"]
        assert record.method == "container_service"
        assert record.enabled is True
        assert record.provisioning_status == "failed"
        assert record.request_id == "550e8400-e29b-41d4-a716-446655440042"
        assert "protocol version mismatch" in (record.provisioning_error or "")

        report = doctor_manifest(manifest, state=load_state())
        provisioning = next(item for item in report.checks if item.name == "provisioning")
        assert provisioning.status == "fail"
        assert provisioning.metadata == {
            "provisioning_status": "failed",
            "request_id": "550e8400-e29b-41d4-a716-446655440042",
        }
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_container_service_doctor_sanitizes_persisted_provisioning_error() -> None:
    manifest = get_manifest("hloc").model_copy(deep=True)
    record_manual_install(
        "hloc",
        method="container_service",
        enabled=True,
        provisioning_status="failed",
        provisioning_error=(
            "container_service health check failed for "
            "http://plugin-hloc/version?X-Amz-Signature=abc at C:/cache/host.json"
        ),
        request_id="550e8400-e29b-41d4-a716-446655440099",
    )

    report = doctor_manifest(manifest, state=load_state())
    provisioning = next(item for item in report.checks if item.name == "provisioning")

    assert provisioning.status == "fail"
    assert provisioning.detail == "task execution failed"
    assert "plugin-hloc" not in json.dumps(provisioning.model_dump())
    assert "C:/cache" not in json.dumps(provisioning.model_dump())


def test_container_service_install_records_after_healthy_protocol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = {
        "/healthz": (200, b'{"status":"ok"}'),
        "/version": (
            200,
            b'{"protocol":"sfmapi-plugin-http-v1","protocol_version":"1.0"}',
        ),
        **_catalog_responses("hloc"),
    }
    server, thread, base_url = _start_container_service(responses)
    try:
        manifest = get_manifest("hloc").model_copy(deep=True)
        manifest.runtime_modes.container_service = ContainerServiceRuntime.model_validate(
            {
                "protocol": "sfmapi-plugin-http-v1",
                "protocol_version": "1.0",
                "service": {"default_url": base_url},
            }
        )
        monkeypatch.setattr(plugin_service, "get_manifest", lambda plugin_id: manifest)

        result = plugin_service.install_plugin(
            "hloc",
            method="container_service",
            dry_run=False,
            allow_unsafe_execution=True,
            request_id="550e8400-e29b-41d4-a716-446655440043",
        )
        record = load_state().installed["hloc"]

        assert result["installed"] is True
        assert result["request_id"] == "550e8400-e29b-41d4-a716-446655440043"
        assert record.method == "container_service"
        assert record.request_id == "550e8400-e29b-41d4-a716-446655440043"
        report = doctor_manifest(manifest, state=load_state())
        container_check = next(item for item in report.checks if item.name == "container_service")
        loadable_check = next(item for item in report.checks if item.name == "loadable")
        assert report.status != "fail"
        assert container_check.status == "pass"
        assert loadable_check.status == "warn"
        assert loadable_check.metadata == {"installed_method": "container_service"}
        responses["/healthz"] = (503, b'{"status":"down"}')

        replay = plugin_service.install_plugin(
            "hloc",
            method="container_service",
            dry_run=False,
            allow_unsafe_execution=True,
            request_id="550e8400-e29b-41d4-a716-446655440043",
        )
        assert replay == result

        with pytest.raises(ValidationError, match="health check returned HTTP 503"):
            plugin_service.install_plugin(
                "hloc",
                method="container_service",
                dry_run=False,
                allow_unsafe_execution=True,
                request_id="550e8400-e29b-41d4-a716-446655440044",
            )
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_container_service_install_provisions_declared_image_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = {
        "/healthz": (200, b'{"status":"ok"}'),
        "/version": (
            200,
            b'{"protocol":"sfmapi-plugin-http-v1","protocol_version":"1.0"}',
        ),
        **_catalog_responses("hloc"),
    }
    calls: list[list[str]] = []
    server, thread, base_url = _start_container_service(responses)
    try:
        manifest = get_manifest("hloc").model_copy(deep=True)
        manifest.runtime_modes.container_service = ContainerServiceRuntime.model_validate(
            {
                "protocol": "sfmapi-plugin-http-v1",
                "protocol_version": "1.0",
                "service": {"default_url": base_url},
                "image": {
                    "build": {
                        "source": "local",
                        "context": "plugins/hloc",
                        "dockerfile": "Dockerfile",
                    }
                },
            }
        )
        monkeypatch.setattr(plugin_service, "get_manifest", lambda plugin_id: manifest)
        monkeypatch.setattr(
            plugin_service,
            "run_install_command",
            lambda plan: calls.append(list(plan.command)),
        )

        result = plugin_service.install_plugin(
            "hloc",
            method="container_service",
            dry_run=False,
            allow_unsafe_execution=True,
            request_id="550e8400-e29b-41d4-a716-446655440045",
        )
        record = load_state().installed["hloc"]

        assert calls == [result["command"]]
        assert result["provision_runtime"] is True
        assert result["provisioned"] is True
        assert result["provisioning_status"] == "succeeded"
        assert record.provision_runtime is True
        assert record.provisioned is True
        assert record.provisioning_status == "succeeded"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_container_service_install_can_attach_without_provisioning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = {
        "/healthz": (200, b'{"status":"ok"}'),
        "/version": (
            200,
            b'{"protocol":"sfmapi-plugin-http-v1","protocol_version":"1.0"}',
        ),
        **_catalog_responses("hloc"),
    }
    calls: list[list[str]] = []
    server, thread, base_url = _start_container_service(responses)
    try:
        manifest = get_manifest("hloc").model_copy(deep=True)
        manifest.runtime_modes.container_service = ContainerServiceRuntime.model_validate(
            {
                "protocol": "sfmapi-plugin-http-v1",
                "protocol_version": "1.0",
                "service": {"default_url": base_url},
                "image": {
                    "build": {
                        "source": "local",
                        "context": "plugins/hloc",
                        "dockerfile": "Dockerfile",
                    }
                },
            }
        )
        monkeypatch.setattr(plugin_service, "get_manifest", lambda plugin_id: manifest)
        monkeypatch.setattr(
            plugin_service,
            "run_install_command",
            lambda plan: calls.append(list(plan.command)),
        )

        result = plugin_service.install_plugin(
            "hloc",
            method="container_service",
            dry_run=False,
            allow_unsafe_execution=True,
            provision_runtime=False,
        )

        assert calls == []
        assert result["installed"] is True
        assert result["provision_runtime"] is False
        assert result["provisioned"] is False
        assert result["provisioning_status"] == "not_requested"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_container_runtime_requests_gpu_for_required_services(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sfm_hub import container_runtime

    calls: list[list[str]] = []
    runtime = ContainerServiceRuntime.model_validate(
        {
            "protocol": "sfmapi-plugin-http-v1",
            "protocol_version": "1.0",
            "service": {"default_url": "http://127.0.0.1:8098"},
            "image": {"image": "ghcr.io/sfmapi/sfmapi-plugin-gsplat:test"},
            "execution": {
                "gpu": "required",
                "env": ["TORCH_DEVICE", "CUDA_VISIBLE_DEVICES"],
            },
        }
    )
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0")
    monkeypatch.setattr(
        container_runtime.subprocess,
        "run",
        lambda command, **kwargs: calls.append(list(command)),
    )

    container_runtime._run_service(
        "gsplat",
        "ghcr.io/sfmapi/sfmapi-plugin-gsplat:test",
        runtime,
        8098,
    )

    run_command = calls[1]
    assert "--gpus" in run_command
    assert run_command[run_command.index("--gpus") + 1] == "all"
    assert "127.0.0.1:8098:8080" in run_command
    assert "-e" in run_command
    assert "TORCH_DEVICE=cuda" in run_command
    assert "CUDA_VISIBLE_DEVICES" in run_command


def test_container_runtime_does_not_inject_torch_device_for_non_torch_gpu_services(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sfm_hub import container_runtime

    calls: list[list[str]] = []
    runtime = ContainerServiceRuntime.model_validate(
        {
            "protocol": "sfmapi-plugin-http-v1",
            "protocol_version": "1.0",
            "service": {"default_url": "http://127.0.0.1:8096"},
            "image": {"image": "ghcr.io/sfmapi/sfmapi-plugin-brush:test"},
            "execution": {
                "gpu": "required",
                "env": ["WGPU_BACKEND", "NVIDIA_VISIBLE_DEVICES", "NVIDIA_DRIVER_CAPABILITIES"],
            },
        }
    )
    monkeypatch.setattr(
        container_runtime.subprocess,
        "run",
        lambda command, **kwargs: calls.append(list(command)),
    )

    container_runtime._run_service(
        "brush",
        "ghcr.io/sfmapi/sfmapi-plugin-brush:test",
        runtime,
        8096,
    )

    run_command = calls[1]
    assert "--gpus" in run_command
    assert "127.0.0.1:8096:8080" in run_command
    assert "NVIDIA_VISIBLE_DEVICES=all" in run_command
    assert "NVIDIA_DRIVER_CAPABILITIES=compute,utility,graphics" in run_command
    assert "TORCH_DEVICE=cuda" not in run_command


def test_container_runtime_allows_container_port_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sfm_hub import container_runtime

    calls: list[list[str]] = []
    runtime = ContainerServiceRuntime.model_validate(
        {
            "protocol": "sfmapi-plugin-http-v1",
            "protocol_version": "1.0",
            "service": {"default_url": "http://127.0.0.1:8096"},
            "image": {"image": "ghcr.io/sfmapi/sfmapi-plugin-brush:test"},
            "execution": {"gpu": "none"},
        }
    )
    monkeypatch.setenv("SFMAPI_PLUGIN_CONTAINER_PORT", "9090")
    monkeypatch.setattr(
        container_runtime.subprocess,
        "run",
        lambda command, **kwargs: calls.append(list(command)),
    )

    container_runtime._run_service(
        "brush",
        "ghcr.io/sfmapi/sfmapi-plugin-brush:test",
        runtime,
        8096,
    )

    assert "127.0.0.1:8096:9090" in calls[1]


def test_docker_install_rejects_missing_runtime_execution() -> None:
    with pytest.raises(ValidationError, match="does not define a docker runtime"):
        plugin_service.install_plugin(
            "hloc",
            method="docker",
            dry_run=False,
            allow_unsafe_execution=True,
        )


def test_no_plugin_advertises_empty_docker_runtime() -> None:
    for manifest in list_manifests():
        runtime = manifest.runtime_modes.docker
        assert runtime is None or runtime.image or runtime.build_context, manifest.plugin_id


def test_provider_resolution_uses_profiles_and_rejects_ambiguity() -> None:
    record_manual_install("colmap_cli", method="external_tool")
    record_manual_install("pycolmap", method="uv")

    with pytest.raises(ProviderAmbiguityError):
        resolve_provider(stage="features", capability="features.extract.sift")

    assert (
        resolve_provider(
            stage="features",
            capability="features.extract.sift",
            requested_provider="colmap_pycolmap",
        )
        == "colmap_pycolmap"
    )

    upsert_profile(
        RoutingProfile(name="prefer-cli", routes={"features": ["colmap_cli@colmap_cli"]}),
    )
    state = load_state()
    state.default_profile = "prefer-cli"
    save_state(state)

    assert (
        resolve_provider(stage="features", capability="features.extract.sift")
        == "colmap_cli@colmap_cli"
    )


def test_provider_records_respects_explicit_empty_manifest_scope() -> None:
    record_manual_install("colmap_cli", method="external_tool")

    assert provider_records(manifests=[]) == []


def test_provider_resolution_ignores_backend_actions_when_capability_requested() -> None:
    record_manual_install("real_radiance", method="uv")
    record_manual_install("action_only_radiance", method="uv")
    real = PluginManifest.model_validate(
        _typed_extension_manifest(
            plugin_id="real_radiance",
            display_name="Real Radiance",
            package_name="sfmapi-real-radiance",
            github_url="https://github.com/SFMAPI/sfmapi_real_radiance",
            entry_points=["sfmapi_real_radiance:plugin"],
            providers=[
                {
                    "provider_id": "real_radiance",
                    "display_name": "Real Radiance",
                    "capabilities": ["radiance.train"],
                }
            ],
            processor_extensions=[],
        )
    )
    action_only = PluginManifest.model_validate(
        _typed_extension_manifest(
            plugin_id="action_only_radiance",
            display_name="Action-only Radiance",
            package_name="sfmapi-action-only-radiance",
            github_url="https://github.com/SFMAPI/sfmapi_action_only_radiance",
            entry_points=["sfmapi_action_only_radiance:plugin"],
            providers=[
                {
                    "provider_id": "action_only_radiance",
                    "display_name": "Action-only Radiance",
                    "capabilities": [],
                    "backend_actions": ["radiance"],
                }
            ],
            capabilities=[],
            backend_actions=["radiance"],
            datatypes=[],
            processors=[],
            pipelines=[],
            processor_extensions=[],
        )
    )

    assert (
        resolve_provider(
            stage="radiance",
            capability="radiance.train",
            manifests=[action_only, real],
        )
        == "real_radiance"
    )
    with pytest.raises(KeyError, match="action_only_radiance"):
        resolve_provider(
            stage="radiance",
            capability="radiance.train",
            requested_provider="action_only_radiance",
            manifests=[action_only, real],
        )
    with pytest.raises(KeyError, match="action_only_radiance"):
        resolve_provider(
            stage="radiance",
            capability="radiance.train",
            requested_provider="action_only_radiance",
            manifests=[action_only],
        )
    set_enabled("action_only_radiance", False)
    with pytest.raises(KeyError, match="disabled"):
        resolve_provider(
            stage="radiance",
            capability="radiance.train",
            requested_provider="action_only_radiance",
            manifests=[action_only],
        )


def test_provider_resolution_rejects_duplicate_provider_id_collisions() -> None:
    record_manual_install("colmap_cli", method="external_tool")
    record_manual_install("colmap_native", method="external_tool")

    with pytest.raises(ProviderAmbiguityError, match="colmap_cli@"):
        resolve_provider(
            stage="features",
            capability="features.extract.sift",
            requested_provider="colmap_cli",
        )


def test_routing_state_rejects_unusable_profile_entries() -> None:
    with pytest.raises(PydanticValidationError, match="unsupported route key"):
        RoutingProfile(name="bad-route", routes={"featurs": ["hloc"]})

    with pytest.raises(KeyError, match="ambiguous provider id"):
        upsert_profile(RoutingProfile(name="bad-provider", routes={"features": ["colmap_cli"]}))

    with pytest.raises(KeyError, match="ambiguous provider id"):
        set_provider_priority(["colmap_cli"])


def test_provider_resolution_accepts_plugin_qualified_selector_for_duplicates() -> None:
    record_manual_install("colmap_cli", method="external_tool")
    record_manual_install("colmap_native", method="external_tool")

    assert (
        resolve_provider(
            stage="features",
            capability="features.extract.sift",
            requested_provider="colmap_cli@colmap_native",
        )
        == "colmap_cli@colmap_native"
    )


def test_routing_profile_can_disambiguate_duplicate_provider_id() -> None:
    record_manual_install("colmap_cli", method="external_tool")
    record_manual_install("colmap_native", method="external_tool")
    upsert_profile(
        RoutingProfile(name="prefer-native", routes={"features": ["colmap_cli@colmap_native"]}),
    )
    state = load_state()
    state.default_profile = "prefer-native"
    save_state(state)

    assert (
        resolve_provider(stage="features", capability="features.extract.sift")
        == "colmap_cli@colmap_native"
    )


def test_routing_profile_can_select_radiance_provider() -> None:
    record_manual_install("gsplat", method="container_service")
    upsert_profile(
        RoutingProfile(name="prefer-gsplat", routes={"radiance": ["gsplat"]}),
    )
    state = load_state()
    state.default_profile = "prefer-gsplat"
    save_state(state)

    assert resolve_provider(stage="radiance", capability="radiance.train") == "gsplat"


def test_provider_priority_can_disambiguate_duplicate_provider_id() -> None:
    record_manual_install("colmap_cli", method="external_tool")
    record_manual_install("colmap_native", method="external_tool")
    state = load_state()
    state.provider_priority = ["colmap_cli@colmap_native"]
    save_state(state)

    assert (
        resolve_provider(stage="features", capability="features.extract.sift")
        == "colmap_cli@colmap_native"
    )


def test_provider_resolution_uses_provider_priority_fallback() -> None:
    record_manual_install("colmap_cli", method="external_tool")
    record_manual_install("pycolmap", method="uv")
    state = load_state()
    state.provider_priority = ["colmap_pycolmap"]
    save_state(state)

    assert (
        resolve_provider(stage="features", capability="features.extract.sift") == "colmap_pycolmap"
    )


def test_disabled_provider_is_rejected_for_runtime_resolution() -> None:
    record_manual_install("hloc", method="entry_point", enabled=False)

    with pytest.raises(KeyError, match="disabled"):
        ensure_provider_enabled("hloc")

    ensure_provider_enabled("not_registered_in_hub")


def test_provider_resolution_uses_project_profile_before_default() -> None:
    record_manual_install("colmap_cli", method="external_tool")
    record_manual_install("pycolmap", method="uv")
    upsert_profile(RoutingProfile(name="default", routes={"features": ["colmap_cli@colmap_cli"]}))
    upsert_profile(
        RoutingProfile(name="project", routes={"features": ["colmap_pycolmap@pycolmap"]})
    )
    state = load_state()
    state.default_profile = "default"
    save_state(state)
    set_project_profile("project-1", "project")

    assert (
        resolve_provider(
            stage="features",
            capability="features.extract.sift",
            project_id="project-1",
        )
        == "colmap_pycolmap@pycolmap"
    )


def test_stage_validation_applies_provider_resolution() -> None:
    record_manual_install("colmap_cli", method="external_tool")
    spec = {"type": "sift", "backend_options": {}}

    sfm_stage_service.validate_features_config(spec)

    assert spec["provider"] == "colmap_cli"


def test_stage_validation_preserves_plugin_qualified_provider() -> None:
    record_manual_install("colmap_cli", method="external_tool")
    record_manual_install("colmap_native", method="external_tool")
    spec = {
        "type": "sift",
        "provider": "colmap_cli@colmap_native",
        "backend_options": {},
    }

    sfm_stage_service.validate_features_config(spec)

    assert spec["provider"] == "colmap_cli@colmap_native"


def test_stage_validation_reports_ambiguous_provider() -> None:
    record_manual_install("colmap_cli", method="external_tool")
    record_manual_install("pycolmap", method="uv")

    with pytest.raises(ValidationError, match="multiple candidate providers"):
        sfm_stage_service.validate_features_config({"type": "sift", "backend_options": {}})


def test_manifest_lookup_returns_expected_install_metadata() -> None:
    manifest = get_manifest("colmap_cli")

    assert manifest.runtime_modes.uv is not None
    assert manifest.runtime_modes.uv.package == "sfmapi-colmap-unified"
    assert "colmap_cli" in manifest.provider_ids()


def test_external_tool_manifests_use_runtime_env_vars() -> None:
    colmap_cli = get_manifest("colmap_cli")
    colmap_native = get_manifest("colmap_native")
    realityscan = get_manifest("realityscan_cli")
    spheresfm = get_manifest("spheresfm")

    assert colmap_cli.runtime_modes.external_tool is not None
    assert colmap_native.runtime_modes.external_tool is not None
    assert realityscan.runtime_modes.external_tool is not None
    assert spheresfm.runtime_modes.external_tool is not None
    assert "SFMAPI_COLMAP_EXECUTABLE" in colmap_cli.runtime_modes.external_tool.env_vars
    assert "SFMAPI_COLMAP_EXECUTABLE" in colmap_native.runtime_modes.external_tool.env_vars
    assert "SFMAPI_RC_EXECUTABLE" in realityscan.runtime_modes.external_tool.env_vars
    assert "SFMAPI_SPHERESFM_EXECUTABLE" in spheresfm.runtime_modes.external_tool.env_vars


def test_upstream_license_metadata_is_specific() -> None:
    upstream = {
        item.name: item.license
        for manifest in list_manifests(include_entry_points=False)
        for item in manifest.upstream_projects
    }

    assert upstream["COLMAP"] == "BSD-3-Clause"
    assert upstream["Hierarchical Localization"] == "Apache-2.0"
    assert upstream["InstantSfM"] == "CC-BY-NC-4.0"
    assert upstream["SphereSfM"] == "BSD-3-Clause"
    assert all(value != "Upstream license" for value in upstream.values())


def test_external_tool_detection_checks_env_and_version(monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = get_manifest("colmap_cli").model_copy(deep=True)
    assert manifest.runtime_modes.external_tool is not None
    manifest.runtime_modes.external_tool.executable_names = []
    manifest.runtime_modes.external_tool.env_vars = ["TEST_TOOL_EXE"]
    manifest.runtime_modes.external_tool.version_args = ["--version"]
    monkeypatch.setenv("TEST_TOOL_EXE", sys.executable)

    tools = detect_external_tools([manifest])["colmap_cli"]

    assert tools[0].source == "env"
    assert tools[0].path == Path(sys.executable).name
    assert tools[0].version


def test_detect_tools_redacts_local_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = get_manifest("colmap_cli").model_copy(deep=True)
    assert manifest.runtime_modes.external_tool is not None
    manifest.runtime_modes.external_tool.executable_names = []
    manifest.runtime_modes.external_tool.env_vars = ["TEST_TOOL_EXE"]
    manifest.runtime_modes.external_tool.version_args = ["--bad-version-arg"]
    monkeypatch.setenv("TEST_TOOL_EXE", sys.executable)

    tools = detect_external_tools([manifest])["colmap_cli"]

    assert tools[0].path == Path(sys.executable).name
    assert sys.executable not in json.dumps(tools[0].model_dump(mode="json"))


def test_entry_point_discovery_loads_plugin_manifest(monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = get_manifest("hloc")

    class FakeEntryPoint:
        name = "hloc"
        value = "fake.module:plugin"
        dist = None

        def load(self) -> PluginManifest:
            return manifest

    class FakeEntryPoints(list[FakeEntryPoint]):
        def select(self, *, group: str) -> list[FakeEntryPoint]:
            assert group == "sfmapi.backends"
            return list(self)

    import sfm_hub.discovery as discovery

    monkeypatch.setattr(
        discovery.metadata, "entry_points", lambda: FakeEntryPoints([FakeEntryPoint()])
    )

    found = discover_plugins(load=True)

    assert found[0].plugin_id == "hloc"
    assert found[0].manifest == manifest


def test_entry_point_load_errors_are_public(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeEntryPoint:
        name = "bad_plugin"
        value = "fake.module:plugin"
        dist = None

        def load(self) -> object:
            raise RuntimeError("failed at C:\\private\\token\\plugin.py")

    class FakeEntryPoints(list[FakeEntryPoint]):
        def select(self, *, group: str) -> list[FakeEntryPoint]:
            assert group == "sfmapi.backends"
            return list(self)

    import sfm_hub.discovery as discovery

    monkeypatch.setattr(
        discovery.metadata, "entry_points", lambda: FakeEntryPoints([FakeEntryPoint()])
    )

    rows = plugin_service.list_entry_points(load=True)

    assert rows[0]["plugin_id"] == "bad_plugin"
    assert rows[0]["load_error"]
    assert "token" not in rows[0]["load_error"].lower()
    assert "C:\\private" not in rows[0]["load_error"]


def test_entry_point_manifest_id_activates_plugin_when_name_differs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = get_manifest("hloc")

    class FakeEntryPoint:
        name = "entry_name"
        value = "fake.module:plugin"
        dist = None

        def load(self) -> PluginManifest:
            return manifest

    class FakeEntryPoints(list[FakeEntryPoint]):
        def select(self, *, group: str) -> list[FakeEntryPoint]:
            assert group == "sfmapi.backends"
            return list(self)

    import sfm_hub.discovery as discovery
    from sfmapi.server.services.dataflow_registry_service import active_manifests

    monkeypatch.setattr(
        discovery.metadata, "entry_points", lambda: FakeEntryPoints([FakeEntryPoint()])
    )

    assert discovery.discovered_plugin_ids() == {"entry_name", "hloc"}
    assert [item.plugin_id for item in active_manifests(manifests=[manifest])] == ["hloc"]
    assert any(
        row.plugin_id == "hloc" and row.provider.provider_id == "hloc"
        for row in provider_records(manifests=[manifest])
    )
    detail = plugin_service.get_plugin("hloc")
    assert detail["installed"] is True
    assert detail["enabled"] is True


def test_entry_point_loader_registers_backend_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    manifest_obj = get_manifest("hloc")

    class PluginObject:
        backend_name = "entry_backend"
        manifest = manifest_obj

        @staticmethod
        def backend_factory() -> object:
            return object()

    class FakeEntryPoint:
        name = "entry_backend"
        value = "fake.module:plugin"
        dist = None

        def load(self) -> PluginObject:
            return PluginObject()

    class FakeEntryPoints(list[FakeEntryPoint]):
        def select(self, *, group: str) -> list[FakeEntryPoint]:
            assert group == "sfmapi.backends"
            return list(self)

    import sfm_hub.discovery as discovery

    monkeypatch.setattr(
        discovery.metadata, "entry_points", lambda: FakeEntryPoints([FakeEntryPoint()])
    )
    registered: dict[str, object] = {}
    providers: dict[str, object] = {}

    def register_backend(name: str, factory: object) -> None:
        registered[name] = factory

    def register_provider(provider_id: str, factory: object) -> None:
        providers[provider_id] = factory

    loaded = load_backend_entry_points(  # type: ignore[arg-type]
        register_backend,
        register_provider=register_provider,
    )

    assert loaded[0].plugin_id == "hloc"
    assert "entry_backend" in registered
    assert providers["hloc"] is registered["entry_backend"]


def test_entry_point_loader_skips_disabled_plugin(monkeypatch: pytest.MonkeyPatch) -> None:
    manifest_obj = get_manifest("hloc")
    record_manual_install("hloc", method="uv")
    set_enabled("hloc", False)

    class PluginObject:
        backend_name = "hloc"
        manifest = manifest_obj

        @staticmethod
        def backend_factory() -> object:
            return object()

    class FakeEntryPoint:
        name = "hloc"
        value = "fake.module:plugin"
        dist = None

        def load(self) -> PluginObject:
            return PluginObject()

    class FakeEntryPoints(list[FakeEntryPoint]):
        def select(self, *, group: str) -> list[FakeEntryPoint]:
            assert group == "sfmapi.backends"
            return list(self)

    import sfm_hub.discovery as discovery

    monkeypatch.setattr(
        discovery.metadata, "entry_points", lambda: FakeEntryPoints([FakeEntryPoint()])
    )
    registered: dict[str, object] = {}
    providers: dict[str, object] = {}

    loaded = load_backend_entry_points(  # type: ignore[arg-type]
        registered.setdefault,
        register_provider=providers.setdefault,
    )

    assert loaded[0].plugin_id == "hloc"
    assert loaded[0].skipped is True
    assert loaded[0].load_error is None
    assert not registered
    assert not providers


def test_entry_point_loader_registrar_accepts_providers_kwarg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Entry-point plugins can declare provider aliases through the
    ``registrar`` callback rather than only through the manifest."""
    manifest_obj = get_manifest("hloc")

    class PluginObject:
        manifest = manifest_obj

        @staticmethod
        def register(registrar) -> None:  # type: ignore[no-untyped-def]
            registrar(
                "explicit_backend",
                lambda: object(),
                providers=["explicit.provider"],
            )

    class FakeEntryPoint:
        name = "explicit_entry"
        value = "fake.module:plugin"
        dist = None

        def load(self) -> PluginObject:
            return PluginObject()

    class FakeEntryPoints(list[FakeEntryPoint]):
        def select(self, *, group: str) -> list[FakeEntryPoint]:
            return list(self)

    import sfm_hub.discovery as discovery

    monkeypatch.setattr(
        discovery.metadata, "entry_points", lambda: FakeEntryPoints([FakeEntryPoint()])
    )
    registered: dict[str, object] = {}
    providers: dict[str, object] = {}

    load_backend_entry_points(  # type: ignore[arg-type]
        registered.setdefault,
        register_provider=providers.setdefault,
    )

    # Callback-declared provider wins; manifest providers (hloc) for the same
    # single backend still register via the manifest fallback path.
    assert "explicit.provider" in providers
    assert providers["explicit.provider"] is registered["explicit_backend"]
    assert "hloc" in providers


def test_entry_point_loader_logs_unmatched_manifest_provider(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """When a plugin registers >1 backend and the manifest lists provider
    ids that don't match any registered backend name, sfm_hub warns
    instead of silently dropping the alias."""
    manifest_obj = get_manifest("hloc")

    class PluginObject:
        manifest = manifest_obj

        @staticmethod
        def register(registrar) -> None:  # type: ignore[no-untyped-def]
            registrar("alpha", lambda: object())
            registrar("beta", lambda: object())

    class FakeEntryPoint:
        name = "multi_entry"
        value = "fake.module:plugin"
        dist = None

        def load(self) -> PluginObject:
            return PluginObject()

    class FakeEntryPoints(list[FakeEntryPoint]):
        def select(self, *, group: str) -> list[FakeEntryPoint]:
            return list(self)

    import sfm_hub.discovery as discovery

    monkeypatch.setattr(
        discovery.metadata, "entry_points", lambda: FakeEntryPoints([FakeEntryPoint()])
    )
    registered: dict[str, object] = {}
    providers: dict[str, object] = {}

    with caplog.at_level("WARNING", logger="sfm_hub.discovery"):
        load_backend_entry_points(  # type: ignore[arg-type]
            registered.setdefault,
            register_provider=providers.setdefault,
        )

    assert {"alpha", "beta"} <= registered.keys()
    # Manifest provider id "hloc" matches neither "alpha" nor "beta" so it
    # must NOT be silently aliased to one of them, and a warning must fire.
    assert "hloc" not in providers
    assert any("unmatched_manifest_provider" in str(record.msg) for record in caplog.records)


def test_plugin_service_enable_records_entry_point_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """enable_plugin on a discovered-but-not-yet-installed entry-point
    plugin records a manual install instead of raising."""
    from sfmapi.server.services import plugin_service

    monkeypatch.setattr(
        "sfmapi.server.services.plugin_service.discovered_plugin_ids",
        lambda: {"hloc"},
    )

    detail = plugin_service.enable_plugin("hloc")

    state = load_state()
    assert "hloc" in state.installed
    assert state.installed["hloc"].method == "entry_point"
    assert state.installed["hloc"].enabled is True
    assert detail["enabled"] is True


def test_plugin_service_disable_records_entry_point_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Symmetric to enable: disable on a discovered-but-not-yet-installed
    entry-point plugin records the manual install (disabled)."""
    from sfmapi.server.services import plugin_service

    monkeypatch.setattr(
        "sfmapi.server.services.plugin_service.discovered_plugin_ids",
        lambda: {"hloc"},
    )

    plugin_service.disable_plugin("hloc")

    state = load_state()
    assert "hloc" in state.installed
    assert state.installed["hloc"].enabled is False


def test_plugin_service_runs_package_provisioner_after_uv_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sfmapi.server.services import plugin_service

    calls: list[str] = []

    def fake_uv_install(plan) -> None:  # type: ignore[no-untyped-def]
        calls.append("uv:" + plan.source.inferred_package)

    def fake_provisioner(
        package_name: str,
        *,
        dry_run: bool,
        force: bool,
    ) -> dict[str, object]:
        calls.append(f"provision:{package_name}:{dry_run}:{force}")
        return {
            "available": True,
            "provisioned": True,
            "steps": [{"name": "engine", "status": "done"}],
            "env": {"ENGINE": "ready"},
            "outputs": {"ENGINE_HOME": "C:/engine"},
            "warnings": [],
        }

    monkeypatch.setattr(plugin_service, "run_uv_install", fake_uv_install)
    monkeypatch.setattr(plugin_service, "run_package_provisioner", fake_provisioner)

    result = plugin_service.install_plugin(
        "local_test",
        github_url="https://github.com/SFMAPI/sfmapi_custom.git@v0.1.0",
        package_name="sfmapi-custom",
        dry_run=False,
        allow_unsafe_execution=True,
    )

    assert result["installed"] is True
    assert result["provisioned"] is True
    assert result["provisioning"]["env_keys"] == ["ENGINE"]
    assert result["provisioning"]["redacted_env"] == {"ENGINE": "<redacted>"}
    assert result["provisioning"]["outputs"] == {"ENGINE_HOME": "<redacted>"}
    assert calls == ["uv:sfmapi-custom", "provision:sfmapi-custom:False:False"]


def test_plugin_service_records_skipped_package_provisioner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sfmapi.server.services import plugin_service

    monkeypatch.setattr(plugin_service, "run_uv_install", lambda plan: None)
    monkeypatch.setattr(
        plugin_service,
        "run_package_provisioner",
        lambda package_name, *, dry_run, force: {
            "available": False,
            "provisioned": False,
            "steps": [{"name": "engine", "status": "skipped"}],
            "env": {},
            "outputs": {},
            "warnings": ["engine not available"],
        },
    )

    result = plugin_service.install_plugin(
        "local_test",
        github_url="https://github.com/SFMAPI/sfmapi_custom.git@v0.1.0",
        package_name="sfmapi-custom",
        dry_run=False,
        allow_unsafe_execution=True,
    )
    record = load_state().installed["local_test"]

    assert result["provisioned"] is False
    assert result["provisioning_status"] == "skipped"
    assert record.provisioning_status == "skipped"


def test_plugin_service_redacts_secret_provisioner_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sfmapi.server.services import plugin_service

    monkeypatch.setattr(plugin_service, "run_uv_install", lambda plan: None)
    monkeypatch.setattr(
        plugin_service,
        "run_package_provisioner",
        lambda package_name, *, dry_run, force: {
            "available": True,
            "provisioned": True,
            "steps": [
                {
                    "name": "token",
                    "api_token": "secret-value",
                    "X-Amz-Signature": "abcdef",
                }
            ],
            "env": {"SFMAPI_PLUGIN_TOKEN": "secret-value"},
            "outputs": {
                "PUBLIC_PATH": "C:/cache",
                "PUBLIC_URL": "https://artifacts.example/model.bin?safe=1;sig=abc",
                "ACCESS_KEY": "secret-value",
                "sig": "abcdef",
                "COUNT": 3,
                "NESTED": {"safe": 1, "cache": "C:/cache/model.bin"},
            },
            "metadata": {
                "artifact": "https://artifacts.example/model.bin?X-Amz-Signature=abc",
                "signature": "abcdef",
                "nested": {"password": "secret-value", "cache": "C:/cache/model.bin"},
            },
            "warnings": ["downloaded C:/cache/model.bin"],
        },
    )

    result = plugin_service.install_plugin(
        "local_test",
        github_url="https://github.com/SFMAPI/sfmapi_custom.git@v0.1.0",
        package_name="sfmapi-custom",
        dry_run=False,
        allow_unsafe_execution=True,
    )
    from sfmapi.server.schemas.api.plugins import PluginInstallResponse

    PluginInstallResponse.model_validate(result)
    serialized = json.dumps(result)

    assert "secret-value" not in serialized
    assert "C:/cache" not in serialized
    assert "X-Amz-Signature" not in serialized
    assert "sig=abc" not in serialized
    assert result["provisioning"]["env_keys"] == ["SFMAPI_PLUGIN_TOKEN"]
    assert result["provisioning"]["redacted_env"] == {"SFMAPI_PLUGIN_TOKEN": "<redacted>"}
    assert result["provisioning"]["outputs"]["ACCESS_KEY"] == "<redacted>"
    assert "sig" not in result["provisioning"]["outputs"]
    assert result["provisioning"]["outputs"]["PUBLIC_PATH"] == "<redacted>"
    assert result["provisioning"]["outputs"]["PUBLIC_URL"] == "<redacted>"
    assert result["provisioning"]["outputs"]["COUNT"] == 3
    assert result["provisioning"]["outputs"]["NESTED"] == {
        "safe": 1,
        "cache": "<redacted>",
    }
    assert result["provisioning"]["steps"][0]["api_token"] == "<redacted>"
    assert "X-Amz-Signature" not in result["provisioning"]["steps"][0]
    assert "signature" not in result["provisioning"]["metadata"]
    assert result["provisioning"]["warnings"] == ["downloaded <redacted>"]


def test_plugin_service_records_failed_provisioning_and_dedupes_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sfm_hub.provision import ProvisioningError
    from sfmapi.server.services import plugin_service

    request_id = "123e4567-e89b-12d3-a456-426614174000"
    calls: list[str] = []

    def fake_uv_install(plan) -> None:  # type: ignore[no-untyped-def]
        calls.append("uv:" + plan.source.inferred_package)

    def fake_provisioner(package_name: str, *, dry_run: bool, force: bool) -> None:
        calls.append(f"provision:{package_name}:{dry_run}:{force}")
        raise ProvisioningError(
            "download failed at C:/cache/model.bin from https://artifacts.example/model.bin?sig=abc"
        )

    monkeypatch.setattr(plugin_service, "run_uv_install", fake_uv_install)
    monkeypatch.setattr(plugin_service, "run_package_provisioner", fake_provisioner)

    with pytest.raises(ValidationError, match="task execution failed"):
        plugin_service.install_plugin(
            "local_test",
            github_url="https://github.com/SFMAPI/sfmapi_custom.git@v0.1.0",
            package_name="sfmapi-custom",
            dry_run=False,
            allow_unsafe_execution=True,
            request_id=request_id,
        )
    state = load_state()
    record = state.installed["local_test"]

    assert record.provisioning_status == "failed"
    assert record.provisioning_error == "task execution failed"
    assert "C:/cache" not in (record.provisioning_error or "")
    assert "sig=abc" not in (record.provisioning_error or "")
    assert record.request_id == request_id
    assert calls == ["uv:sfmapi-custom", "provision:sfmapi-custom:False:False"]

    with pytest.raises(ValidationError, match=r"previous attempt|task execution failed"):
        plugin_service.install_plugin(
            "local_test",
            github_url="https://github.com/SFMAPI/sfmapi_custom.git@v0.1.0",
            package_name="sfmapi-custom",
            dry_run=False,
            allow_unsafe_execution=True,
            request_id=request_id,
        )

    assert calls == ["uv:sfmapi-custom", "provision:sfmapi-custom:False:False"]


def test_plugin_service_dedupes_successful_install_request_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sfmapi.server.services import plugin_service

    request_id = "123e4567-e89b-12d3-a456-426614174000"
    calls: list[str] = []

    monkeypatch.setattr(
        plugin_service,
        "run_uv_install",
        lambda plan: calls.append("uv:" + plan.source.inferred_package),
    )
    monkeypatch.setattr(
        plugin_service,
        "run_package_provisioner",
        lambda package_name, *, dry_run, force: (
            calls.append(f"provision:{package_name}")
            or {
                "available": True,
                "provisioned": True,
                "steps": [{"name": "engine", "status": "done"}],
                "env": {},
                "warnings": [],
            }
        ),
    )

    first = plugin_service.install_plugin(
        "local_test",
        github_url="https://github.com/SFMAPI/sfmapi_custom.git@v0.1.0",
        package_name="sfmapi-custom",
        dry_run=False,
        allow_unsafe_execution=True,
        request_id=request_id,
    )
    second = plugin_service.install_plugin(
        "local_test",
        github_url="https://github.com/SFMAPI/sfmapi_custom.git@v0.1.0",
        package_name="sfmapi-custom",
        dry_run=False,
        allow_unsafe_execution=True,
        request_id=request_id,
    )

    assert first["provisioning_status"] == "succeeded"
    assert second["provisioning_status"] == "succeeded"
    assert calls == ["uv:sfmapi-custom", "provision:sfmapi-custom"]


def test_plugin_service_reruns_install_with_different_request_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sfmapi.server.services import plugin_service

    calls: list[str] = []

    monkeypatch.setattr(
        plugin_service,
        "run_uv_install",
        lambda plan: calls.append("uv:" + plan.source.inferred_package),
    )
    monkeypatch.setattr(
        plugin_service,
        "run_package_provisioner",
        lambda package_name, *, dry_run, force: (
            calls.append(f"provision:{package_name}")
            or {
                "available": True,
                "provisioned": True,
                "steps": [{"name": "engine", "status": "done"}],
                "env": {},
                "warnings": [],
            }
        ),
    )

    for request_id in [
        "123e4567-e89b-12d3-a456-426614174000",
        "123e4567-e89b-12d3-a456-426614174001",
    ]:
        plugin_service.install_plugin(
            "local_test",
            github_url="https://github.com/SFMAPI/sfmapi_custom.git@v0.1.0",
            package_name="sfmapi-custom",
            dry_run=False,
            allow_unsafe_execution=True,
            request_id=request_id,
        )

    assert calls == [
        "uv:sfmapi-custom",
        "provision:sfmapi-custom",
        "uv:sfmapi-custom",
        "provision:sfmapi-custom",
    ]


def test_plugin_service_no_provision_runtime_records_not_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sfmapi.server.services import plugin_service

    monkeypatch.setattr(plugin_service, "run_uv_install", lambda plan: None)

    result = plugin_service.install_plugin(
        "local_test",
        github_url="https://github.com/SFMAPI/sfmapi_custom.git@v0.1.0",
        package_name="sfmapi-custom",
        dry_run=False,
        allow_unsafe_execution=True,
        provision_runtime=False,
    )
    record = load_state().installed["local_test"]

    assert result["provisioning_status"] == "not_requested"
    assert record.provisioning_status == "not_requested"
