"""MCP <-> REST parity guards (lean audit 2026-07, item 6.5).

``sceneapi/server/mcp/tools.py`` is a hand-maintained mirror of a curated subset of
the REST surface. Nothing structural ties a tool to the route it
mirrors, so the two can drift silently: a tool gets added without a
REST counterpart, a mirrored route gets renamed or deleted while the
tool keeps calling the old service seam, or a hand-rolled tool payload
loses fields the REST ``response_model`` still advertises.

These guards make that drift loud and cheap:

* ``TOOL_TO_REST`` explicitly maps every registered MCP tool to the
  REST route(s) it mirrors. Adding / removing / renaming a tool without
  updating the map fails — drift becomes a conscious choice recorded in
  this file.
* Every mapped route is resolved against the live FastAPI route table,
  so a REST rename or deletion that strands a tool also fails.
* For the highest-drift-risk read tools, the tool's payload key set is
  compared against the mirrored route's ``response_model`` field set —
  the same typed source SDK codegen consumes.

Deliberately out of scope: value-level equality (REST decorates
``_links``, MCP leaves them null), request-parameter parity, and deep
recursive shape checks — the SDK contract fixtures own those.

The module must pass without the optional ``sfmapi[mcp]`` extra: only
the registration-parity test imports ``fastmcp`` (via ``importorskip``),
mirroring ``tests/unit/test_mcp_tools.py``; everything else works from
``sceneapi.server.mcp.tools`` / ``sceneapi.server.mcp.server`` module state, which never import
FastMCP at import time.
"""

from __future__ import annotations

import functools
from datetime import UTC, datetime
from typing import Any, get_args

import pytest
from fastapi.routing import APIRoute
from pydantic import BaseModel

from sceneapi.server.core.ids import new_id
from sceneapi.server.db.models import Task
from sceneapi.server.mcp import tools
from sceneapi.server.mcp.server import TOOL_TITLES
from sceneapi.server.services import job_service, project_service

pytestmark = pytest.mark.contract

# ---------------------------------------------------------------------
# The coverage map. Every MCP tool name -> the REST route(s) it
# hand-mirrors, as (HTTP method, FastAPI path template) pairs.
#
# ``list_portable_stages`` is a curated catalog with no single REST
# counterpart; each catalog entry names its own route and is validated
# in ``test_portable_stage_catalog_points_at_live_routes``.
#
# When this test fails after you add / remove / rename an MCP tool or
# move a REST route: update this map (and the mirroring code) in the
# same change, deliberately.
# ---------------------------------------------------------------------
TOOL_TO_REST: dict[str, tuple[tuple[str, str], ...]] = {
    "sfmapi_version": (("GET", "/version"),),
    "sfmapi_capabilities": (("GET", "/v1/capabilities"),),
    "list_portable_stages": (),
    "list_backend_actions": (("GET", "/v1/backend/actions"),),
    "get_backend_action": (("GET", "/v1/backend/actions/{action_id}"),),
    "list_plugins": (("GET", "/v1/admin/plugins"),),
    "get_plugin": (("GET", "/v1/admin/plugins/{plugin_id}"),),
    "doctor_plugin": (("POST", "/v1/admin/plugins/{plugin_id}:doctor"),),
    "list_backend_providers": (("GET", "/v1/backend/providers"),),
    "plan_plugin_install": (("POST", "/v1/admin/plugins/{plugin_id}:install"),),
    "list_projects": (("GET", "/v1/projects"),),
    "list_jobs": (("GET", "/v1/jobs"),),
    "get_job": (("GET", "/v1/jobs/{job_id}"),),
    "get_job_progress": (("GET", "/v1/jobs/{job_id}/progress"),),
    "list_artifacts": (
        ("GET", "/v1/jobs/{job_id}/artifacts"),
        ("GET", "/v1/reconstructions/{recon_id}/artifacts"),
    ),
    "get_artifact": (("GET", "/v1/artifacts/{artifact_id}"),),
    "list_artifact_formats": (("GET", "/v1/artifacts/formats"),),
    "validate_artifact": (("POST", "/v1/artifacts/{artifact_id}:validate"),),
    "plan_artifact_conversion": (("POST", "/v1/artifacts/{artifact_id}:conversionPlan"),),
    "get_reconstruction": (("GET", "/v1/reconstructions/{recon_id}"),),
    "list_submodels": (("GET", "/v1/reconstructions/{recon_id}/submodels"),),
    "list_snapshots": (("GET", "/v1/reconstructions/{recon_id}/snapshots"),),
}


def _registered_tool_names() -> list[str]:
    return [tool.__name__ for tool in tools.TOOLS]


@functools.cache
def _rest_route_index() -> dict[tuple[str, str], APIRoute]:
    """(method, path template) -> APIRoute for the full FastAPI app.

    Route templates are settings-independent, so building the app once
    per test process is safe and keeps the whole module fast.
    """
    from sceneapi.server.main import create_app

    index: dict[tuple[str, str], APIRoute] = {}
    for route in create_app().routes:
        if isinstance(route, APIRoute):
            for method in route.methods or ():
                index[(method, route.path)] = route
    return index


def _route_response_model(method: str, path: str) -> type[BaseModel]:
    route = _rest_route_index().get((method, path))
    assert route is not None, f"no REST route {method} {path}"
    model = route.response_model
    assert model is not None, f"REST route {method} {path} declares no response_model"
    return model


def _serialized_keys(model_cls: type[BaseModel]) -> set[str]:
    """Top-level JSON keys a by-alias dump of ``model_cls`` produces."""
    keys: set[str] = set()
    for name, field in model_cls.model_fields.items():
        if field.exclude:
            continue
        keys.add(field.serialization_alias or field.alias or name)
    return keys


def _list_item_model(model_cls: type[BaseModel], field_name: str) -> type[BaseModel]:
    """Resolve the item model of a ``list[Model]`` field (e.g. Page.items)."""
    annotation = model_cls.model_fields[field_name].annotation
    (item_model,) = get_args(annotation)
    assert isinstance(item_model, type)
    assert issubclass(item_model, BaseModel)
    return item_model


def _assert_page_parity(payload: dict[str, Any], method: str, path: str) -> None:
    """Payload keys == route page-envelope keys; first item == item model keys."""
    page_model = _route_response_model(method, path)
    assert set(payload) == _serialized_keys(page_model), (
        f"MCP payload keys drifted from {method} {path} response_model"
    )
    assert payload["items"], f"parity check for {method} {path} needs at least one item"
    item_model = _list_item_model(page_model, "items")
    assert set(payload["items"][0]) == _serialized_keys(item_model), (
        f"MCP item keys drifted from {method} {path} item model {item_model.__name__}"
    )


async def _seed_project_and_job(session) -> tuple[str, str]:
    """Minimal rows so list/get tools return at least one of everything."""
    project = await project_service.create_project(
        session,
        tenant_id="default",
        name="mcp-parity",
        description="MCP parity seed",
    )
    job = await job_service.create_job(
        session,
        tenant_id="default",
        project_id=project.project_id,
        recipe="incremental",
        spec={"kind": "incremental"},
    )
    session.add(
        Task(
            task_id=new_id(),
            tenant_id="default",
            job_id=job.job_id,
            kind="match",
            inputs_hash="inputs",
            params_hash="params",
            runtime_version_id="rv",
            cache_key="cache",
            status="running",
            started_at=datetime.now(UTC),
        )
    )
    await session.commit()
    return project.project_id, job.job_id


def test_every_mcp_tool_maps_to_a_live_rest_route() -> None:
    """Invariant: the MCP surface is a conscious mirror of REST.

    Enumerates the registered tools from ``sceneapi.server.mcp.tools.TOOLS`` (no
    hardcoded count) and requires a ``TOOL_TO_REST`` entry for each, in
    both directions. Each mapped (method, path) must exist in the live
    FastAPI route table. Catches: adding a tool without recording what
    it mirrors, removing/renaming a tool while the map still lists it,
    and renaming/deleting a REST route out from under its MCP mirror.
    """
    tool_names = _registered_tool_names()
    assert len(set(tool_names)) == len(tool_names), "duplicate tool names in TOOLS"

    unmapped = set(tool_names) - set(TOOL_TO_REST)
    assert not unmapped, (
        f"MCP tools with no REST parity mapping: {sorted(unmapped)} — "
        "add them to TOOL_TO_REST in this test (drift must be a conscious choice)"
    )
    stale = set(TOOL_TO_REST) - set(tool_names)
    assert not stale, (
        f"TOOL_TO_REST names tools that are no longer registered: {sorted(stale)} — "
        "remove the entries or restore the tools in sceneapi/server/mcp/tools.py"
    )

    routes = _rest_route_index()
    for tool_name, rest_routes in TOOL_TO_REST.items():
        for method, path in rest_routes:
            assert (method, path) in routes, (
                f"MCP tool {tool_name!r} mirrors {method} {path}, but that REST route "
                "no longer exists — update the tool and this map together"
            )


async def test_portable_stage_catalog_points_at_live_routes() -> None:
    """Invariant: every ``list_portable_stages`` catalog entry is real.

    The catalog in ``sceneapi/server/mcp/tools.py`` hand-copies stage route
    templates ("keep in sync with sceneapi/server/api/v1/..." per its comment).
    Each entry's (method, route) must exist in the FastAPI route table
    and live under the resource prefix its ``scope`` claims. Catches a
    stage route being renamed/removed while the catalog keeps
    advertising it to agents.
    """
    routes = _rest_route_index()
    catalog = await tools.list_portable_stages()
    stages = catalog["items"]
    assert stages, "portable-stage catalog is empty"

    names = [stage["stage"] for stage in stages]
    assert len(set(names)) == len(names), "duplicate stage names in the catalog"

    prefix_by_scope = {
        "dataset": "/v1/datasets/{dataset_id}",
        "reconstruction": "/v1/reconstructions/{recon_id}",
    }
    for stage in stages:
        assert (stage["method"], stage["route"]) in routes, (
            f"portable stage {stage['stage']!r} advertises {stage['method']} "
            f"{stage['route']}, which is not a live REST route"
        )
        assert stage["route"].startswith(prefix_by_scope[stage["scope"]]), (
            f"portable stage {stage['stage']!r} claims scope {stage['scope']!r} "
            f"but its route is {stage['route']}"
        )


def test_tool_titles_reference_only_registered_tools() -> None:
    """Invariant: ``TOOL_TITLES`` never names a tool that doesn't exist.

    Titles are optional decoration (``list_portable_stages`` currently
    ships without one, so the reverse direction is intentionally not
    asserted), but a title keyed on a removed/renamed tool is dead
    weight that signals a half-finished rename in ``sceneapi/server/mcp/server.py``.
    """
    assert set(TOOL_TITLES) <= set(_registered_tool_names())


async def test_discovery_tool_payload_keys_match_rest_response_models() -> None:
    """Invariant: hand-rolled discovery payloads track the REST models.

    ``sfmapi_capabilities`` serializes via ``Capabilities.as_dict()``
    while REST serializes via ``CapabilitiesOut`` — two code paths that
    only agree by discipline; key equality (both directions, plus the
    ``backend`` sub-object) pins them together. ``sfmapi_version`` and
    ``list_artifact_formats`` dump the same models the routes declare,
    so equality also detects a route swapping to a different model.
    """
    version = await tools.sfmapi_version()
    assert set(version) == _serialized_keys(_route_response_model("GET", "/version"))

    capabilities = await tools.sfmapi_capabilities()
    caps_model = _route_response_model("GET", "/v1/capabilities")
    assert set(capabilities) == _serialized_keys(caps_model), (
        "Capabilities.as_dict() drifted from CapabilitiesOut"
    )
    backend_model = caps_model.model_fields["backend"].annotation
    assert isinstance(backend_model, type)
    assert issubclass(backend_model, BaseModel)
    assert set(capabilities["backend"]) == _serialized_keys(backend_model), (
        "BackendInfo.as_dict() drifted from BackendInfoOut"
    )

    formats = await tools.list_artifact_formats()
    _assert_page_parity(formats, "GET", "/v1/artifacts/formats")


async def test_job_and_project_tool_payload_keys_match_rest_response_models(session) -> None:
    """Invariant: the project/job read tools return REST-shaped payloads.

    These tools rebuild their REST handlers' logic by hand (same
    service calls, same Pydantic models, duplicated glue), so the drift
    risk is a tool quietly diverging from the route's declared
    ``response_model``. Seeds one project + job + task, calls each tool
    directly (as ``tests/unit/test_mcp_tools.py`` does), and asserts
    key-set equality with the mirrored route's response model —
    including page items and ``JobDetail.tasks`` rows.
    """
    _project_id, job_id = await _seed_project_and_job(session)

    projects = await tools.list_projects()
    _assert_page_parity(projects, "GET", "/v1/projects")

    jobs = await tools.list_jobs()
    _assert_page_parity(jobs, "GET", "/v1/jobs")

    detail = await tools.get_job(job_id)
    detail_model = _route_response_model("GET", "/v1/jobs/{job_id}")
    assert set(detail) == _serialized_keys(detail_model), (
        "get_job payload drifted from the JobDetail response model"
    )
    assert detail["tasks"], "seed produced a job without tasks"
    task_model = _list_item_model(detail_model, "tasks")
    assert set(detail["tasks"][0]) == _serialized_keys(task_model), (
        "get_job task rows drifted from the TaskOut wire shape"
    )

    progress = await tools.get_job_progress(job_id)
    progress_model = _route_response_model("GET", "/v1/jobs/{job_id}/progress")
    assert set(progress) == _serialized_keys(progress_model), (
        "get_job_progress payload drifted from the JobProgressOut response model"
    )


async def test_fastmcp_registration_mirrors_module_tool_list() -> None:
    """Invariant: the served MCP surface == ``sceneapi.server.mcp.tools.TOOLS``.

    Registers the real FastMCP server and asserts the advertised tool
    names are exactly the module registration list, every tool carries
    the read-only annotation contract (MCP mirrors REST reads only —
    mutations stay REST/SDK), and every tool ships a description
    (docstring) for agents. Skips cleanly when the optional ``mcp``
    extra is not installed, like the real-FastMCP tests in
    ``tests/unit/test_mcp_tools.py``.
    """
    pytest.importorskip("fastmcp")

    from sceneapi.server.mcp.server import create_mcp_server

    server = create_mcp_server()
    listed = await server.list_tools()

    assert {tool.name for tool in listed} == set(_registered_tool_names())
    for tool in listed:
        assert tool.description, f"MCP tool {tool.name!r} has no description docstring"
        assert tool.annotations.readOnlyHint is True, f"{tool.name!r} lost readOnlyHint"
        assert tool.annotations.destructiveHint is False, f"{tool.name!r} lost destructiveHint"
