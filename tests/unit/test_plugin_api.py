from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.unit


def _start_container_service(
    responses: dict[str, tuple[int, bytes]],
) -> tuple[ThreadingHTTPServer, Thread, str]:
    class Handler(BaseHTTPRequestHandler):
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

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, thread, f"http://{host}:{port}"


async def test_plugin_registry_admin_api_and_provider_discovery(client: AsyncClient) -> None:
    plugins = await client.get("/v1/admin/plugins")
    assert plugins.status_code == 200, plugins.text
    items = plugins.json()["items"]
    assert any(item["plugin_id"] == "colmap_cli" for item in items)
    assert all(item["installed"] is False for item in items)

    plan = await client.post(
        "/v1/admin/plugins/colmap_cli:install",
        json={"method": "uv", "dry_run": True},
    )
    assert plan.status_code == 200, plan.text
    assert plan.json()["direct_reference"].startswith("sfmapi-colmap-cli @ git+")
    assert plan.json()["installed"] is False
    assert plan.json()["provision_runtime"] is True

    install = await client.post(
        "/v1/admin/plugins/colmap_cli:install",
        json={"method": "external_tool", "dry_run": False, "allow_unsafe_execution": True},
    )
    assert install.status_code == 200, install.text
    assert install.json()["installed"] is True

    providers = await client.get("/v1/backend/providers")
    assert providers.status_code == 200, providers.text
    assert [item["provider_id"] for item in providers.json()["items"]] == ["colmap_cli"]

    disable = await client.post("/v1/admin/plugins/colmap_cli:disable")
    assert disable.status_code == 200, disable.text
    assert disable.json()["enabled"] is False

    providers = await client.get("/v1/backend/providers")
    assert providers.status_code == 200, providers.text
    assert providers.json()["items"] == []


async def test_plugin_admin_accepts_github_install_source(client: AsyncClient) -> None:
    response = await client.post(
        "/v1/admin/plugins/local_test:install",
        json={
            "method": "uv",
            "github_url": "https://github.com/SFMAPI/sfmapi_custom.git@v0.1.0",
            "package_name": "sfmapi-custom",
            "dry_run": True,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["plugin_id"] == "local_test"
    assert body["direct_reference"] == (
        "sfmapi-custom @ git+https://github.com/SFMAPI/sfmapi_custom.git@v0.1.0"
    )
    assert body["provisioning"]["steps"][0]["status"] == "planned"


async def test_plugin_admin_install_redacts_provisioning_env(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import plugin_service

    monkeypatch.setattr(plugin_service, "run_uv_install", lambda plan: None)
    monkeypatch.setattr(
        plugin_service,
        "run_package_provisioner",
        lambda package_name, *, dry_run, force: {
            "available": True,
            "provisioned": True,
            "steps": [{"name": "secret", "api_key": "secret-value"}],
            "env": {"SFMAPI_PLUGIN_TOKEN": "secret-value"},
            "outputs": {"PUBLIC_PATH": "C:/cache", "API_KEY": "secret-value"},
            "warnings": [],
        },
    )

    response = await client.post(
        "/v1/admin/plugins/local_test:install",
        json={
            "method": "uv",
            "github_url": "https://github.com/SFMAPI/sfmapi_custom.git@v0.1.0",
            "package_name": "sfmapi-custom",
            "dry_run": False,
            "allow_unsafe_execution": True,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert "secret-value" not in json.dumps(body)
    assert body["provisioning"]["env_keys"] == ["SFMAPI_PLUGIN_TOKEN"]
    assert body["provisioning"]["redacted_env"] == {"SFMAPI_PLUGIN_TOKEN": "<redacted>"}


async def test_plugin_admin_accepts_container_service_runtime_choice(
    client: AsyncClient,
) -> None:
    response = await client.post(
        "/v1/admin/plugins/hloc:install",
        json={"method": "container_service", "dry_run": True},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["method"] == "container_service"
    assert body["command"] == []
    assert body["warnings"] == ["plugin 'hloc' does not define a container_service runtime"]


async def test_plugin_admin_container_service_runtime_flow(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import plugin_service
    from sfm_hub.models import ContainerServiceRuntime
    from sfm_hub.registry import get_manifest
    from sfm_hub.routing import ProviderRecord

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
        monkeypatch.setattr(plugin_service, "get_manifest", lambda plugin_id: manifest)

        install = await client.post(
            "/v1/admin/plugins/hloc:install",
            json={
                "method": "container_service",
                "dry_run": False,
                "allow_unsafe_execution": True,
            },
        )
        assert install.status_code == 200, install.text
        assert install.json()["installed"] is True

        doctor = await client.post("/v1/admin/plugins/hloc:doctor")
        assert doctor.status_code == 200, doctor.text
        check = next(item for item in doctor.json()["checks"] if item["name"] == "container_service")
        assert check["status"] == "pass"
        assert check["metadata"]["protocol"] == "sfmapi-plugin-http-v1"

        monkeypatch.setattr(
            plugin_service,
            "provider_records",
            lambda: [
                ProviderRecord(
                    plugin_id="hloc",
                    installed=True,
                    enabled=True,
                    runtime_modes=manifest.runtime_mode_names(),
                    provider=manifest.providers[0],
                )
            ],
        )
        providers = await client.get("/v1/backend/providers")
        assert providers.status_code == 200, providers.text
        assert providers.json()["items"][0]["runtime_modes"] == [
            "uv",
            "container_service",
        ]

        disable = await client.post("/v1/admin/plugins/hloc:disable")
        assert disable.status_code == 200, disable.text
        assert disable.json()["enabled"] is False

        enable = await client.post("/v1/admin/plugins/hloc:enable")
        assert enable.status_code == 200, enable.text
        assert enable.json()["enabled"] is True
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


async def test_plugin_admin_rejects_unsafe_http_install_without_opt_in(
    client: AsyncClient,
) -> None:
    response = await client.post(
        "/v1/admin/plugins/colmap_cli:install",
        json={"method": "external_tool", "dry_run": False},
    )

    assert response.status_code == 422
    assert "allow_unsafe_execution" in response.json()["detail"]


async def test_plugin_doctor_and_tool_detection(client: AsyncClient) -> None:
    doctor = await client.post("/v1/admin/plugins/colmap_cli:doctor")
    assert doctor.status_code == 200, doctor.text
    assert doctor.json()["plugin_id"] == "colmap_cli"
    assert doctor.json()["checks"][0]["name"] == "manifest"

    tools = await client.get("/v1/admin/plugins/detect-tools")
    assert tools.status_code == 200, tools.text
    assert "colmap_cli" in tools.json()["tools"]

    entry_points = await client.get("/v1/admin/plugins/entry-points")
    assert entry_points.status_code == 200, entry_points.text
    assert entry_points.json()["items"] == []


async def test_backend_routing_endpoint_is_available(client: AsyncClient) -> None:
    routing = await client.get("/v1/backend/routing")

    assert routing.status_code == 200, routing.text
    assert routing.json()["default_profile"] is None
    assert routing.json()["profiles"] == {}


async def test_admin_routing_profile_assignment_api(client: AsyncClient) -> None:
    priority = await client.post(
        "/v1/admin/routing/provider-priority",
        json={"providers": ["colmap_cli"]},
    )
    assert priority.status_code == 200, priority.text
    assert priority.json()["provider_priority"] == ["colmap_cli"]

    profile = await client.post(
        "/v1/admin/routing/profiles",
        json={"name": "hybrid", "routes": {"features": ["colmap_cli"]}},
    )
    assert profile.status_code == 200, profile.text
    assert "hybrid" in profile.json()["profiles"]

    default = await client.post("/v1/admin/routing/default", json={"profile": "hybrid"})
    assert default.status_code == 200, default.text
    assert default.json()["default_profile"] == "hybrid"

    project = await client.post(
        "/v1/admin/routing/projects/project-1",
        json={"profile": "hybrid"},
    )
    assert project.status_code == 200, project.text
    assert project.json()["project_profiles"]["project-1"] == "hybrid"
