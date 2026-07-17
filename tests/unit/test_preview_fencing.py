"""Preview-tier API fencing (SPEC §1.3 [Preview]; lean audit D1 / 7.1).

The preview surfaces — admin routing profiles, typed-dataflow
discovery, and image similarity — stay mounted and serving in every
deployment. ``settings.expose_preview_apis`` (env
``SCENEAPI_EXPOSE_PREVIEW_APIS``) only controls whether they appear in
the OpenAPI document, i.e. whether they are part of the default
(kernel) contract that SDK codegen and the pinned
``docs/_static/openapi.json`` snapshot see.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from sceneapi.server.core.config import reset_settings_for_tests
from sceneapi.server.main import PREVIEW_CONFORMANCE_KEY, PREVIEW_CONFORMANCE_VALUE, create_app

pytestmark = pytest.mark.unit

HTTP_METHODS = {"get", "post", "put", "patch", "delete"}

# Every operation fenced behind ``expose_preview_apis`` (path -> methods).
PREVIEW_OPERATIONS: dict[str, set[str]] = {
    "/v1/admin/routing/profiles": {"post"},
    "/v1/admin/routing/default": {"post"},
    "/v1/admin/routing/provider-priority": {"post"},
    "/v1/admin/routing/projects/{project_id}": {"post"},
    "/v1/admin/routing/workspaces": {"post"},
    "/v1/datatypes": {"get"},
    "/v1/attributes": {"get"},
    "/v1/operations": {"get"},
    "/v1/processors": {"get"},
    "/v1/pipelines": {"get"},
    "/v1/pipelines:validate": {"post"},
    "/v1/datasets/{dataset_id}/similarity": {"get"},
    "/v1/datasets/{dataset_id}/similarity:build": {"post"},
}


def _app(expose_preview_apis: bool | None = None) -> FastAPI:
    """Build the app with a fresh Settings instance.

    ``None`` means "whatever the environment says" (used to prove the
    env-var wiring); a bool overrides explicitly.
    """
    if expose_preview_apis is None:
        reset_settings_for_tests()
    else:
        reset_settings_for_tests(expose_preview_apis=expose_preview_apis)
    return create_app()


def _op_count(spec: dict) -> int:
    return sum(
        1 for path_item in spec["paths"].values() for method in path_item if method in HTTP_METHODS
    )


def test_default_contract_excludes_preview_operations() -> None:
    spec = _app().openapi()
    paths = spec["paths"]

    leaked = sorted(set(PREVIEW_OPERATIONS) & set(paths))
    assert not leaked, f"preview paths leaked into the default OpenAPI contract: {leaked}"
    # Belt and braces on the whole namespaces, not just the known list.
    assert not [p for p in paths if p.startswith("/v1/admin/routing")]
    assert not [p for p in paths if "/similarity" in p]

    # The kernel surface is untouched — including the parts adjacent to
    # the fence: API-key admin stays (auth is core) and the Core
    # `:run` preflight route from SPEC §6.8.2 stays.
    assert "/v1/admin/api-keys" in paths
    assert "/v1/admin/plugins" in paths
    assert "/v1/projects/{project_id}/pipelines:run" in paths
    assert "/v1/projects" in paths


def test_preview_routes_stay_mounted_and_serving_by_default() -> None:
    app = _app()

    route_index: dict[str, set[str]] = {}
    for route in app.routes:
        if isinstance(route, APIRoute):
            methods = {m.lower() for m in route.methods or ()}
            route_index.setdefault(route.path, set()).update(methods)
    for path, methods in PREVIEW_OPERATIONS.items():
        assert methods <= route_index.get(path, set()), f"preview route unmounted: {path}"


async def test_preview_routes_respond_on_default_app() -> None:
    """Fencing is OpenAPI-only: the default app still executes the
    preview handlers. One live probe per fenced area, each with a
    deterministic status that requires no DB schema (404 would mean
    the route was actually removed)."""
    app = _app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/v1/datatypes")
        assert resp.status_code == 200
        assert resp.json()["contract"] == "datatypes"

        resp = await client.post(
            "/v1/pipelines:validate",
            json={"steps": ["features", "pairs", "matches", "verify", "map"]},
        )
        assert resp.status_code == 200

        # Body-validation 422s prove the route matched and executed
        # (an unrouted path would 404, a wrong method 405).
        resp = await client.post("/v1/admin/routing/profiles", json={})
        assert resp.status_code == 422

        resp = await client.get("/v1/datasets/any/similarity")  # missing ?image_id=
        assert resp.status_code == 422


def test_exposed_contract_includes_preview_operations_with_conformance_marker() -> None:
    default_spec = _app().openapi()
    exposed_spec = _app(expose_preview_apis=True).openapi()
    paths = exposed_spec["paths"]

    for path, methods in PREVIEW_OPERATIONS.items():
        assert path in paths, f"preview path missing from exposed contract: {path}"
        for method in methods:
            op = paths[path][method]
            assert op.get(PREVIEW_CONFORMANCE_KEY) == PREVIEW_CONFORMANCE_VALUE, (
                f"{method.upper()} {path} lost its {PREVIEW_CONFORMANCE_KEY} marker"
            )

    # Kernel operations never carry the preview marker.
    assert PREVIEW_CONFORMANCE_KEY not in paths["/v1/projects"]["get"]
    assert PREVIEW_CONFORMANCE_KEY not in paths["/v1/admin/api-keys"]["post"]

    # Exposing the preview tier adds exactly the fenced operations.
    fenced_ops = sum(len(methods) for methods in PREVIEW_OPERATIONS.values())
    assert _op_count(exposed_spec) - _op_count(default_spec) == fenced_ops


def test_expose_preview_apis_env_var_is_wired(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCENEAPI_EXPOSE_PREVIEW_APIS", "true")
    spec = _app(expose_preview_apis=None).openapi()
    missing = sorted(set(PREVIEW_OPERATIONS) - set(spec["paths"]))
    assert not missing, f"SCENEAPI_EXPOSE_PREVIEW_APIS=true did not expose: {missing}"
