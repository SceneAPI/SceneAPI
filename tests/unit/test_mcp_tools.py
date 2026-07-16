from __future__ import annotations

import json
import os
import sys
import types
from datetime import UTC, datetime
from typing import Any, ClassVar

import pytest

from app.core.config import get_settings
from app.core.errors import TenantViolationError
from app.core.ids import new_id
from app.db.models import JobEvent, StageArtifact, Task
from app.mcp import tools
from app.services import job_service, project_service
from sfm_hub.state import record_manual_install


class _FakeFastMCP:
    created: ClassVar[list[_FakeFastMCP]] = []

    def __init__(self, name: str, **kwargs: Any) -> None:
        self.name = name
        self.kwargs = kwargs
        self.tools: dict[str, dict[str, Any]] = {}
        self.resources: dict[str, dict[str, Any]] = {}
        self.routes: list[tuple[str, tuple[str, ...]]] = []
        self.runs: list[dict[str, Any]] = []
        self.created.append(self)

    def tool(self, func: Any, **kwargs: Any) -> Any:
        self.tools[func.__name__] = kwargs
        return func

    def resource(self, uri: str, **kwargs: Any) -> Any:
        def decorator(func: Any) -> Any:
            self.resources[uri] = kwargs
            return func

        return decorator

    def custom_route(self, path: str, methods: list[str]) -> Any:
        def decorator(func: Any) -> Any:
            self.routes.append((path, tuple(methods)))
            return func

        return decorator

    def run(self, **kwargs: Any) -> None:
        self.runs.append(kwargs)


async def _seed_project_job_and_progress(session) -> tuple[str, str, str]:
    project = await project_service.create_project(
        session,
        tenant_id="default",
        name="mcp-demo",
        description="MCP test project",
    )
    job = await job_service.create_job(
        session,
        tenant_id="default",
        project_id=project.project_id,
        recipe="incremental",
        spec={"kind": "incremental"},
    )
    job.status = "running"
    task = Task(
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
    session.add(task)
    session.add(
        StageArtifact(
            tenant_id="default",
            job_id=job.job_id,
            task_id=task.task_id,
            kind="matches.verified.v1",
            name="verified",
            uri="memory://database.db",
            metadata_json={
                "artifact_format": "sfmapi.matches.verified.v1",
                "datatype": "match_graph",
                "schema_version": 1,
            },
        )
    )
    session.add(
        JobEvent(
            event_id=1,
            job_id=job.job_id,
            ts=datetime.now(UTC),
            payload_json={
                "kind": "phase_progress",
                "task_id": task.task_id,
                "phase": "matching",
                "current": 1,
                "total": 4,
            },
        )
    )
    await session.commit()
    return project.project_id, job.job_id, task.task_id


@pytest.mark.unit
async def test_mcp_tools_read_projects_jobs_and_progress(session) -> None:
    project_id, job_id, task_id = await _seed_project_job_and_progress(session)

    projects = await tools.list_projects()
    assert projects["items"][0]["project_id"] == project_id

    jobs = await tools.list_jobs(status="running")
    assert jobs["items"][0]["job_id"] == job_id

    detail = await tools.get_job(job_id)
    assert detail["tasks"][0]["task_id"] == task_id

    progress = await tools.get_job_progress(job_id)
    assert progress["progress"] == 0.25
    assert progress["current_phase"] == "matching"
    assert progress["tasks"][0]["current"] == 1

    artifacts = await tools.list_artifacts(job_id=job_id, kind="matches.verified.v1")
    assert artifacts["items"][0]["name"] == "verified"

    artifact = await tools.get_artifact(artifacts["items"][0]["artifact_id"])
    assert artifact["kind"] == "matches.verified.v1"

    formats = await tools.list_artifact_formats()
    assert "sfmapi.matches.verified.v1" in {item["format_id"] for item in formats["items"]}

    validation = await tools.validate_artifact(artifact["artifact_id"])
    assert validation["valid"] is True

    plan = await tools.plan_artifact_conversion(
        artifact["artifact_id"],
        to_format="sfmapi.matches.verified.v1",
    )
    assert plan["conversion_required"] is False


@pytest.mark.unit
async def test_mcp_version_and_capabilities_tools() -> None:
    version = await tools.sfmapi_version()
    assert version["sfmapi"]

    capabilities = await tools.sfmapi_capabilities()
    assert capabilities["schema_version"] == 1
    assert capabilities["features"]["jobs.read"] is True


@pytest.mark.unit
async def test_mcp_plugin_tools_expose_providers_doctor_and_redacted_install_plan() -> None:
    record_manual_install("hloc", method="uv", enabled=True)

    plugins = await tools.list_plugins()
    hloc = next(item for item in plugins["items"] if item["plugin_id"] == "hloc")
    assert hloc["installed"] is True
    assert hloc["enabled"] is True

    detail = await tools.get_plugin("hloc")
    assert detail["manifest"]["plugin_id"] == "hloc"
    assert detail["installed"] is True

    providers = await tools.list_backend_providers()
    provider_ids = {item["provider_id"] for item in providers["items"]}
    assert "hloc" in provider_ids

    doctor = await tools.doctor_plugin("hloc")
    assert doctor["plugin_id"] == "hloc"
    assert all("metadata" in check for check in doctor["checks"])

    request_id = "123e4567-e89b-12d3-a456-426614174000"
    plan = await tools.plan_plugin_install(
        "local_test",
        github_url="https://github.com/SFMAPI/sfmapi_custom.git@v0.1.0",
        package_name="sfmapi-custom",
        request_id=request_id,
    )
    assert plan["dry_run"] is True
    assert plan["installed"] is False
    assert plan["request_id"] == request_id
    assert plan["provisioning_status"] == "planned"
    assert "env" not in plan["provisioning"]
    assert plan["provisioning"]["env_keys"] == []
    assert plan["provisioning"]["redacted_env"] == {}
    assert plan["provisioning"]["outputs"] == {}


@pytest.mark.unit
def test_mcp_tenant_scope_rejects_cross_tenant_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "auth_mode", "api_key")
    monkeypatch.setattr(settings, "mcp_tenant_id", "tenant-a")

    assert tools.resolve_tenant(None) == "tenant-a"
    assert tools.resolve_tenant("tenant-a") == "tenant-a"
    with pytest.raises(TenantViolationError):
        tools.resolve_tenant("tenant-b")


@pytest.mark.unit
def test_mcp_tenant_scope_required_for_api_key_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "auth_mode", "api_key")
    monkeypatch.setattr(settings, "mcp_tenant_id", None)

    with pytest.raises(TenantViolationError):
        tools.resolve_tenant(None)


@pytest.mark.unit
def test_create_mcp_server_requires_mcp_tenant_for_api_key_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "auth_mode", "api_key")
    monkeypatch.setattr(settings, "mcp_tenant_id", None)

    from app.mcp.server import create_mcp_server

    with pytest.raises(TenantViolationError):
        create_mcp_server()


@pytest.mark.unit
def test_create_mcp_server_registers_curated_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_module = types.SimpleNamespace(FastMCP=_FakeFastMCP)
    monkeypatch.setitem(sys.modules, "fastmcp", fake_module)

    from app.mcp.server import create_mcp_server

    server = create_mcp_server(include_index_route=True, endpoint_hint="/agent")

    assert "get_job_progress" in server.tools
    assert "list_artifacts" in server.tools
    assert "list_artifact_formats" in server.tools
    assert "validate_artifact" in server.tools
    assert "plan_artifact_conversion" in server.tools
    assert "list_plugins" in server.tools
    assert "get_plugin" in server.tools
    assert "doctor_plugin" in server.tools
    assert "list_backend_providers" in server.tools
    assert "plan_plugin_install" in server.tools
    assert "list_projects" in server.tools
    assert "list_portable_stages" in server.tools
    assert server.kwargs["strict_input_validation"] is False
    assert "read-only local adapter" in server.kwargs["instructions"].lower()
    assert server.tools["get_job_progress"]["annotations"]["readOnlyHint"] is True
    assert server.tools["get_job_progress"]["annotations"]["destructiveHint"] is False
    assert server.tools["get_job_progress"]["annotations"]["idempotentHint"] is True
    assert server.tools["get_job_progress"]["annotations"]["openWorldHint"] is False
    assert "sfmapi://version" in server.resources
    assert "sfmapi://artifacts/formats" in server.resources
    assert "sfmapi://plugins" in server.resources
    assert "sfmapi://plugins/{plugin_id}" in server.resources
    assert "sfmapi://backend/providers" in server.resources
    assert "sfmapi://tenants/{tenant_id}/jobs/{job_id}/progress" in server.resources
    assert "sfmapi://tenants/{tenant_id}/jobs/{job_id}/artifacts" in server.resources
    assert server.resources["sfmapi://version"]["annotations"]["readOnlyHint"] is True
    assert ("/", ("GET",)) in server.routes
    assert ("/status", ("GET",)) in server.routes
    assert ("/healthz", ("GET",)) in server.routes


@pytest.mark.unit
def test_mcp_http_requires_explicit_non_loopback_opt_in() -> None:
    from app.mcp.server import main

    with pytest.raises(SystemExit):
        main(["--transport", "http", "--host", "0.0.0.0"])


@pytest.mark.unit
def test_mcp_standalone_defaults_to_http_when_mode_is_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _FakeFastMCP.created.clear()
    fake_module = types.SimpleNamespace(FastMCP=_FakeFastMCP)
    monkeypatch.setitem(sys.modules, "fastmcp", fake_module)

    from app.core.config import reset_settings_for_tests
    from app.mcp.server import main

    reset_settings_for_tests(mcp_mode="http")
    try:
        main(["--host", "127.0.0.1", "--port", "9100"])
    finally:
        reset_settings_for_tests()

    assert _FakeFastMCP.created[-1].runs == [
        {"transport": "http", "host": "127.0.0.1", "port": 9100, "path": "/mcp"}
    ]


@pytest.mark.unit
def test_mcp_standalone_loads_backend_plugins(
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    _FakeFastMCP.created.clear()
    fake_module = types.SimpleNamespace(FastMCP=_FakeFastMCP)
    monkeypatch.setitem(sys.modules, "fastmcp", fake_module)
    calls: list[tuple[Any, Any]] = []

    import sfm_hub.discovery as discovery
    from app.core.config import reset_settings_for_tests
    from app.mcp.server import main

    monkeypatch.setattr(
        discovery,
        "load_backend_entry_points",
        lambda register_backend, register_provider: (
            print("plugin stdout") or calls.append((register_backend, register_provider)) or []
        ),
    )
    reset_settings_for_tests(auto_load_backend_plugins=True)
    try:
        main(["--transport", "stdio"])
    finally:
        reset_settings_for_tests()

    captured = capfd.readouterr()
    assert "plugin stdout" not in captured.out
    assert "plugin stdout" in captured.err
    assert len(calls) == 1
    assert _FakeFastMCP.created[-1].runs == [{"transport": "stdio"}]


@pytest.mark.unit
def test_mcp_stdio_warms_backend_before_serving(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeFastMCP.created.clear()
    fake_module = types.SimpleNamespace(FastMCP=_FakeFastMCP)
    monkeypatch.setitem(sys.modules, "fastmcp", fake_module)
    calls: list[str] = []

    import app.mcp.server as mcp_server

    monkeypatch.setattr(
        mcp_server,
        "_load_backend_plugins_for_standalone",
        lambda *, stdio: calls.append(f"load:{stdio}"),
    )
    monkeypatch.setattr(
        mcp_server,
        "_warm_stdio_backend_runtime",
        lambda: calls.append("warm"),
    )

    mcp_server.main(["--transport", "stdio"])

    assert calls == ["load:True", "warm"]
    assert _FakeFastMCP.created[-1].runs == [{"transport": "stdio"}]


@pytest.mark.unit
def test_sfmapi_serve_cli_sets_mcp_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    fake_uvicorn = types.SimpleNamespace(
        run=lambda app_ref, **kwargs: calls.append((app_ref, kwargs))
    )
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)
    monkeypatch.delenv("SFMAPI_MCP_MODE", raising=False)

    from app.cli import main

    main(["serve", "--mcp", "local", "--host", "127.0.0.1", "--port", "8123"])

    assert os.environ["SFMAPI_MCP_MODE"] == "local"
    assert calls == [
        (
            "sfmapi.runtime:create_app",
            {"factory": True, "host": "127.0.0.1", "port": 8123, "reload": False},
        )
    ]


@pytest.mark.unit
async def test_real_fastmcp_lists_annotations_and_reads_resources(session) -> None:
    pytest.importorskip("fastmcp")
    project_id, job_id, _task_id = await _seed_project_job_and_progress(session)

    from app.mcp.server import create_mcp_server

    server = create_mcp_server()
    listed_tools = await server.list_tools()
    by_name = {tool.name: tool for tool in listed_tools}
    assert by_name["get_job_progress"].annotations.readOnlyHint is True
    assert by_name["get_job_progress"].annotations.destructiveHint is False
    assert by_name["get_job_progress"].annotations.idempotentHint is True
    assert by_name["get_job_progress"].annotations.openWorldHint is False

    resources = await server.list_resources()
    resource_uris = {str(resource.uri) for resource in resources}
    assert "sfmapi://version" in resource_uris
    assert "sfmapi://artifacts/formats" in resource_uris
    assert "sfmapi://plugins" in resource_uris
    assert "sfmapi://backend/providers" in resource_uris

    templates = await server.list_resource_templates()
    template_uris = {template.uri_template for template in templates}
    assert "sfmapi://plugins/{plugin_id}" in template_uris
    assert "sfmapi://tenants/{tenant_id}/jobs/{job_id}/progress" in template_uris
    assert "sfmapi://tenants/{tenant_id}/jobs/{job_id}/artifacts" in template_uris

    version = await server.read_resource("sfmapi://version")
    version_body = json.loads(version.contents[0].content)
    assert version_body["sfmapi"]

    projects = await server.read_resource("sfmapi://tenants/default/projects")
    projects_body = json.loads(projects.contents[0].content)
    assert projects_body["items"][0]["project_id"] == project_id

    plugins = await server.read_resource("sfmapi://plugins")
    plugins_body = json.loads(plugins.contents[0].content)
    assert "hloc" in {item["plugin_id"] for item in plugins_body["items"]}

    progress = await server.read_resource(f"sfmapi://tenants/default/jobs/{job_id}/progress")
    progress_body = json.loads(progress.contents[0].content)
    assert progress_body["progress"] == 0.25

    artifacts = await server.read_resource(f"sfmapi://tenants/default/jobs/{job_id}/artifacts")
    artifacts_body = json.loads(artifacts.contents[0].content)
    assert artifacts_body["items"][0]["kind"] == "matches.verified.v1"


@pytest.mark.unit
async def test_real_fastmcp_client_can_call_tool_and_read_resource(session) -> None:
    pytest.importorskip("fastmcp")
    _project_id, job_id, _task_id = await _seed_project_job_and_progress(session)
    record_manual_install("hloc", method="uv", enabled=True)

    from fastmcp import Client

    from app.mcp.server import create_mcp_server

    async with Client(create_mcp_server()) as client:
        tools_list = await client.list_tools()
        assert any(tool.name == "get_job_progress" for tool in tools_list)

        result = await client.call_tool("get_job_progress", {"job_id": job_id})
        progress_body = json.loads(result.content[0].text)
        assert progress_body["progress"] == 0.25

        artifacts_result = await client.call_tool("list_artifacts", {"job_id": job_id})
        artifacts_body = json.loads(artifacts_result.content[0].text)
        assert artifacts_body["items"][0]["name"] == "verified"

        providers_result = await client.call_tool("list_backend_providers", {})
        providers_body = json.loads(providers_result.content[0].text)
        assert "hloc" in {item["provider_id"] for item in providers_body["items"]}

        install_result = await client.call_tool(
            "plan_plugin_install",
            {
                "plugin_id": "local_test",
                "github_url": "https://github.com/SFMAPI/sfmapi_custom.git@v0.1.0",
                "package_name": "sfmapi-custom",
                "request_id": "123e4567-e89b-12d3-a456-426614174000",
            },
        )
        install_body = json.loads(install_result.content[0].text)
        assert install_body["provisioning"]["redacted_env"] == {}
        assert "env" not in install_body["provisioning"]

        resource = await client.read_resource(f"sfmapi://tenants/default/jobs/{job_id}/progress")
        resource_body = json.loads(resource[0].text)
        assert resource_body["job_id"] == job_id


@pytest.mark.unit
async def test_fastapi_mcp_mount_status_uses_configured_path() -> None:
    pytest.importorskip("fastmcp")
    from httpx import ASGITransport, AsyncClient

    from app.core.config import reset_settings_for_tests
    from app.main import create_app

    reset_settings_for_tests(mcp_enabled=True, mcp_mount_path="/agent")
    try:
        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/agent/status")
    finally:
        reset_settings_for_tests()

    assert response.status_code == 200
    assert "/agent" in response.text


@pytest.mark.unit
async def test_fastapi_mcp_is_not_mounted_by_default() -> None:
    from httpx import ASGITransport, AsyncClient

    from app.core.config import reset_settings_for_tests
    from app.main import create_app

    reset_settings_for_tests(mcp_mode="off", mcp_enabled=False)
    try:
        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/mcp/status")
    finally:
        reset_settings_for_tests(mcp_mode="off", mcp_enabled=False)

    assert response.status_code == 404


@pytest.mark.unit
async def test_fastapi_mcp_mode_local_mounts_and_advertises_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("fastmcp")
    from httpx import ASGITransport, AsyncClient

    from app.adapters.registry import register_backend
    from app.adapters.stub_backend import StubBackend
    from app.core.config import reset_settings_for_tests
    from app.main import create_app

    register_backend("stub", StubBackend)
    monkeypatch.setenv("SFMAPI_BACKEND", "stub")
    reset_settings_for_tests(mcp_mode="local", mcp_mount_path="/agent")
    try:
        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            status_response = await client.get("/agent/status")
            backend_response = await client.get("/v1/backend")
    finally:
        reset_settings_for_tests()

    assert status_response.status_code == 200
    assert backend_response.status_code == 200
    links = backend_response.json()["_links"]
    assert links["mcp"]["href"] == "/agent"
    assert links["mcp_status"]["href"] == "/agent/status"
