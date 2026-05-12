from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.unit


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
