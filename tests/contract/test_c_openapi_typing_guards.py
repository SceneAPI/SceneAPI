"""Regression guards: certain endpoints MUST advertise typed
responses in the OpenAPI spec, otherwise SDK codegen falls back to
``Any`` and clients lose typing.

Each guard here corresponds to a route the contract layer relies on.
Adding a new guard documents that the route's response shape is
part of the wire contract and shouldn't drift back to a raw
``JSONResponse``.
"""

from __future__ import annotations

import pytest

from app.core.config import reset_settings_for_tests
from app.main import create_app
from app.schemas.pipeline_spec import (
    PROVIDER_SELECTOR_MAX_LENGTH,
    PROVIDER_SELECTOR_PATTERN,
)

pytestmark = pytest.mark.contract


def _preview_exposed_app():
    """App with the Preview tier included in the OpenAPI document.

    Preview operations (SPEC §1.3: typed dataflow, similarity, admin
    routing) are fenced out of the default document, so SDK codegen
    only ever sees them when ``expose_preview_apis`` is set. Guards on
    their typed shapes therefore evaluate the exposed contract — the
    invariant guarded (typed response models, closed components) is
    unchanged.
    """
    reset_settings_for_tests(expose_preview_apis=True)
    return create_app()


def _typed_response_schema_ref(spec: dict, path: str, method: str) -> str | None:
    """Return the ``$ref`` of the 200-response schema for an endpoint
    if one is set. Returns None if the endpoint has no typed schema
    (codegen would fall back to Any)."""
    op = spec["paths"].get(path, {}).get(method, {})
    resp200 = op.get("responses", {}).get("200", {})
    schema = resp200.get("content", {}).get("application/json", {}).get("schema", {})
    return schema.get("$ref")


def test_capabilities_endpoint_has_typed_response() -> None:
    app = create_app()
    spec = app.openapi()
    ref = _typed_response_schema_ref(spec, "/v1/capabilities", "get")
    assert ref is not None, (
        "/v1/capabilities lost its response_model — SDK codegen will fall "
        "back to Any. Re-add response_model=CapabilitiesOut on the route."
    )
    assert ref.endswith("/CapabilitiesOut"), f"unexpected ref: {ref}"


def test_capabilities_schema_includes_schema_version() -> None:
    """The wire-version field must stay in the OpenAPI schema or
    cross-language clients can't negotiate envelope shape changes."""
    app = create_app()
    spec = app.openapi()
    cap_schema = spec["components"]["schemas"]["CapabilitiesOut"]
    assert "schema_version" in cap_schema["properties"]
    assert "backend" in cap_schema["properties"]
    assert "features" in cap_schema["properties"]


def _component_ref(spec: dict, ref: str) -> dict:
    """Resolve a ``#/components/schemas/X`` reference."""
    assert ref.startswith("#/components/schemas/"), ref
    return spec["components"]["schemas"][ref.rsplit("/", 1)[-1]]


def _string_schema(schema: dict) -> dict:
    for key in ("anyOf", "oneOf"):
        if key in schema:
            return next(item for item in schema[key] if item.get("type") == "string")
    return schema


def test_readyz_endpoint_has_typed_response() -> None:
    app = create_app()
    spec = app.openapi()
    ref = _typed_response_schema_ref(spec, "/readyz", "get")
    assert ref is not None, "/readyz must keep response_model=ReadyzResponse"
    schema = _component_ref(spec, ref)
    assert "status" in schema["properties"]
    assert "checks" in schema["properties"]


def test_spec_endpoint_has_typed_response() -> None:
    app = create_app()
    spec = app.openapi()
    ref = _typed_response_schema_ref(spec, "/spec", "get")
    assert ref is not None, "/spec must keep response_model=SpecResponse"
    schema = _component_ref(spec, ref)
    for key in ("spec", "spec_version", "spec_url", "openapi_url", "server"):
        assert key in schema["properties"], f"SpecResponse missing {key}"


def test_features_endpoint_returns_job_accepted() -> None:
    app = create_app()
    spec = app.openapi()
    op = spec["paths"]["/v1/datasets/{dataset_id}/features"]["post"]
    # 202 schema must be JobAcceptedResponse-shaped.
    schema = op["responses"]["202"]["content"]["application/json"]["schema"]
    ref = schema.get("$ref", "")
    assert ref.endswith("/JobAcceptedResponse"), f"unexpected ref: {ref}"
    body = _component_ref(spec, ref)
    assert "job_id" in body["properties"]
    assert "task_ids" in body["properties"]


def test_image_exif_has_typed_response() -> None:
    app = create_app()
    spec = app.openapi()
    ref = _typed_response_schema_ref(spec, "/v1/images/{image_id}/exif", "get")
    assert ref is not None, "/v1/images/{id}/exif must be typed"
    assert ref.endswith("/ImageExifResponse")


def test_localize_endpoint_returns_job_accepted() -> None:
    app = create_app()
    spec = app.openapi()
    op = spec["paths"]["/v1/reconstructions/{recon_id}/localize"]["post"]
    schema = op["responses"]["202"]["content"]["application/json"]["schema"]
    assert schema.get("$ref", "").endswith("/JobAcceptedResponse")


def test_pipelines_endpoint_returns_job_accepted() -> None:
    app = create_app()
    spec = app.openapi()
    op = spec["paths"]["/v1/projects/{project_id}/pipelines/{recipe}"]["post"]
    schema = op["responses"]["202"]["content"]["application/json"]["schema"]
    assert schema.get("$ref", "").endswith("/JobAcceptedResponse")


def test_pipeline_run_documents_legacy_success_and_typed_executor_gate() -> None:
    app = create_app()
    spec = app.openapi()
    op = spec["paths"]["/v1/projects/{project_id}/pipelines:run"]["post"]
    success = op["responses"]["202"]["content"]["application/json"]["schema"]
    assert success.get("$ref", "").endswith("/JobAcceptedResponse")
    assert op["responses"]["501"]["description"] != "Successful Response"
    schema = op["responses"]["501"]["content"]["application/problem+json"]["schema"]
    assert schema.get("$ref", "").endswith("/ProblemResponse")
    assert "native typed dag execution" in op["description"].lower()


def test_dataflow_contract_components_are_closed() -> None:
    # Dataflow discovery is Preview tier; its components only appear in
    # the exposed contract, which is exactly what codegen consumes then.
    app = _preview_exposed_app()
    spec = app.openapi()
    for name in (
        "DataTypeOut",
        "AttributeOut",
        "PortSpecOut",
        "ProcessorOut",
        "DataTypesContractOut",
        "AttributesContractOut",
        "ProcessorsContractOut",
    ):
        assert spec["components"]["schemas"][name].get("additionalProperties") is False


def test_new_and_plugin_request_components_are_closed() -> None:
    app = create_app()
    spec = app.openapi()
    for name in (
        "IssueKeyBody",
        "ProjectionSampling",
        "ProjectionOutputOptions",
        "CubemapProjectionSpec",
        "EquirectangularProjectionSpec",
        "PerspectiveViewSpec",
        "PerspectiveProjectionSpec",
        "ProjectionJobRequest",
        "Rotation",
        "Rigid3",
        "Sim3",
        "GpsCoord",
        "ImuMeasurement",
        "PosePrior",
        "FeaturesSpec",
        "PairsSpec",
        "MatcherSpec",
        "VerifySpec",
    ):
        assert spec["components"]["schemas"][name].get("additionalProperties") is False


def test_problem_responses_advertise_problem_json_media_type() -> None:
    app = create_app()
    spec = app.openapi()
    offenders: list[str] = []
    for path, methods in spec["paths"].items():
        for method, op in methods.items():
            if not isinstance(op, dict):
                continue
            for code, response in op.get("responses", {}).items():
                content = response.get("content", {})
                problem = content.get("application/problem+json", {}).get("schema", {})
                if problem.get("$ref", "").endswith("/ProblemResponse"):
                    continue
                json_schema = content.get("application/json", {}).get("schema", {})
                if json_schema.get("$ref", "").endswith("/ProblemResponse"):
                    offenders.append(f"{method.upper()} {path} {code}")
    assert offenders == []


def test_non_2xx_responses_are_not_described_as_successful() -> None:
    app = create_app()
    spec = app.openapi()
    offenders: list[str] = []
    for path, methods in spec["paths"].items():
        for method, op in methods.items():
            for code, response in op.get("responses", {}).items():
                if (
                    not code.startswith("2")
                    and response.get("description") == "Successful Response"
                ):
                    offenders.append(f"{method.upper()} {path} {code}")
    assert offenders == []


def test_common_runtime_problem_responses_are_documented() -> None:
    app = create_app()
    spec = app.openapi()
    response = spec["paths"]["/v1/projects/{project_id}"]["get"]["responses"]["404"]
    schema = response["content"]["application/problem+json"]["schema"]

    assert response["description"] == "Resource not found."
    assert schema.get("$ref", "").endswith("/ProblemResponse")


def test_backend_action_endpoints_are_typed() -> None:
    app = create_app()
    spec = app.openapi()
    ref = _typed_response_schema_ref(spec, "/v1/backend", "get")
    assert ref is not None
    assert ref.endswith("/BackendOut")
    ref = _typed_response_schema_ref(spec, "/v1/backend/actions", "get")
    assert ref is not None
    assert ref.endswith("/Page_BackendActionOut_")
    ref = _typed_response_schema_ref(spec, "/v1/backend/actions/{action_id}", "get")
    assert ref is not None
    assert ref.endswith("/BackendActionOut")
    ref = _typed_response_schema_ref(spec, "/v1/backend/config-schemas", "get")
    assert ref is not None
    assert ref.endswith("/Page_BackendConfigSchemaOut_")
    ref = _typed_response_schema_ref(spec, "/v1/backend/config-schemas/{config_id}", "get")
    assert ref is not None
    assert ref.endswith("/BackendConfigSchemaOut")
    run_op = spec["paths"]["/v1/backend/actions/{action_id}:run"]["post"]
    schema = run_op["responses"]["202"]["content"]["application/json"]["schema"]
    assert schema.get("$ref", "").endswith("/JobAcceptedResponse")


def test_snapshot_list_has_typed_response() -> None:
    app = create_app()
    spec = app.openapi()
    ref = _typed_response_schema_ref(spec, "/v1/reconstructions/{recon_id}/snapshots", "get")
    assert ref is not None
    assert ref.endswith("/SnapshotListResponse")


def test_image_observations_has_typed_response() -> None:
    app = create_app()
    spec = app.openapi()
    ref = _typed_response_schema_ref(
        spec,
        "/v1/reconstructions/{recon_id}/snapshots/{seq}/images/{image_id}/observations",
        "get",
    )
    assert ref is not None
    assert ref.endswith("/ImageObservationsResponse")


def test_similarity_endpoints_typed() -> None:
    # Similarity is Preview tier — typed-shape guard runs against the
    # exposed contract (the only document that carries the route).
    app = _preview_exposed_app()
    spec = app.openapi()
    ref = _typed_response_schema_ref(spec, "/v1/datasets/{dataset_id}/similarity", "get")
    assert ref is not None
    assert ref.endswith("/SimilarityQueryResponse")


def test_routed_provider_surfaces_use_plugin_selector_contract() -> None:
    # similarity:build is Preview tier (MergeRequest is kernel and
    # identical in both documents) — evaluate the exposed contract.
    app = _preview_exposed_app()
    spec = app.openapi()

    build_op = spec["paths"]["/v1/datasets/{dataset_id}/similarity:build"]["post"]
    build_provider = next(
        param["schema"] for param in build_op["parameters"] if param["name"] == "provider"
    )
    build_provider = _string_schema(build_provider)
    assert build_provider["pattern"] == PROVIDER_SELECTOR_PATTERN
    assert build_provider["maxLength"] == PROVIDER_SELECTOR_MAX_LENGTH

    merge_schema = spec["components"]["schemas"]["MergeRequest"]["properties"]["provider"]
    merge_provider = _string_schema(merge_schema)
    assert merge_provider["pattern"] == PROVIDER_SELECTOR_PATTERN
    assert merge_provider["maxLength"] == PROVIDER_SELECTOR_MAX_LENGTH


def test_plugin_manifest_openapi_denies_core_shadow_ids() -> None:
    app = create_app()
    spec = app.openapi()
    schemas = spec["components"]["schemas"]

    datatype_id = schemas["PluginDataTypeManifest"]["properties"]["type_id"]
    processor_id = schemas["PluginProcessorManifest"]["properties"]["processor_id"]
    pipeline_id = schemas["PluginPipelineManifest"]["properties"]["pipeline_id"]

    assert "sparse_model" in datatype_id["not"]["enum"]
    assert "features" in processor_id["not"]["enum"]
    assert "sfm" in pipeline_id["not"]["enum"]


def test_artifact_sha_fields_advertise_lowercase_hex_pattern() -> None:
    app = create_app()
    spec = app.openapi()
    schemas = spec["components"]["schemas"]
    for component, field in (
        ("ArtifactFileRef", "sha256"),
        ("ArtifactImportRequest", "sha256"),
        ("StageArtifactOut", "sha256"),
    ):
        schema = _string_schema(schemas[component]["properties"][field])
        assert schema["pattern"] == "^[0-9a-f]{64}$"


def test_artifact_conversion_targets_are_standard_schema_visible() -> None:
    app = create_app()
    spec = app.openapi()
    schemas = spec["components"]["schemas"]

    for component in ("ArtifactConversionPlanRequest", "ArtifactConvertRequest"):
        schema = schemas[component]
        assert schema["if"]["properties"]["to_format"] == {"type": "null"}
        assert schema["then"]["required"] == ["accepted_formats"]
        assert schema["then"]["properties"]["accepted_formats"] == {
            "type": "array",
            "minItems": 1,
        }
        assert schema["properties"]["accepted_formats"]["minItems"] == 1
        assert schema["x-sfmapi-target-requirement"].startswith("at least one")


def test_no_regression_in_untyped_route_count() -> None:
    """Ensure new untyped routes don't slip in. As of 2026-05 the only
    routes without a JSON ``response_model`` are intentionally
    non-JSON: 204 deletes, binary file streams (`*.bin`,
    `points_preview.bin`, `bytes`, `thumbnail`), the SSE event stream,
    and large precomputed JSON files served as ``FileResponse``.

    If this test fails after adding a new endpoint, either:
      1. add ``response_model=...`` to the route, or
      2. confirm the route is intentionally non-JSON (binary / SSE /
         FileResponse) and bump the limit here.
    """
    app = create_app()
    spec = app.openapi()
    untyped = 0
    for _path, methods in spec["paths"].items():
        for method, op in methods.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            responses = op.get("responses", {})
            for code, resp in responses.items():
                if not code.startswith("2"):
                    continue
                content = resp.get("content", {}).get("application/json", {})
                schema = content.get("schema", {})
                is_untyped = (
                    not schema
                    or schema == {}
                    or (
                        schema.get("type") == "object"
                        and not schema.get("$ref")
                        and not schema.get("properties")
                    )
                )
                if is_untyped:
                    untyped += 1
                    break
    # Update this number ONLY when adding a new genuinely-non-JSON
    # route (binary / SSE / FileResponse). Adding a normal JSON route
    # without response_model should fail this test.
    assert untyped <= 17, (
        f"untyped route count is {untyped}, expected ≤ 17. "
        "Add response_model to the new route, or update this limit "
        "if the route is intentionally non-JSON."
    )
