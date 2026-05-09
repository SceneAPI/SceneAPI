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

from app.main import create_app

pytestmark = pytest.mark.contract


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
    app = create_app()
    spec = app.openapi()
    ref = _typed_response_schema_ref(spec, "/v1/datasets/{dataset_id}/similarity", "get")
    assert ref is not None
    assert ref.endswith("/SimilarityQueryResponse")


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
    assert untyped <= 16, (
        f"untyped route count is {untyped}, expected ≤ 16. "
        "Add response_model to the new route, or update this limit "
        "if the route is intentionally non-JSON."
    )
