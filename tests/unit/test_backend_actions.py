from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.adapters.registry import register_backend
from app.adapters.stub_backend import StubBackend
from app.core.capabilities import detect_capabilities, reset_capabilities_cache
from app.core.config import reset_settings_for_tests

pytestmark = pytest.mark.unit


class EchoBackend(StubBackend):
    name = "echo"
    version = "1.0"
    vendor = "tests"

    def list_backend_actions(self) -> list[dict[str, Any]]:
        return [
            {
                "action_id": "echo.echo",
                "display_name": "Echo",
                "description": "Return the input payload.",
                "category": "diagnostics",
                "side_effects": "none",
                "long_running": False,
                "idempotent": True,
                "gpu_required": False,
                "input_schema": {
                    "type": "object",
                    "required": ["message"],
                    "properties": {"message": {"type": "string"}},
                },
            }
        ]

    def validate_backend_action(self, action_id: str, inputs: dict[str, Any]) -> dict[str, Any]:
        if action_id != "echo.echo":
            return {"valid": False, "errors": [{"message": "unknown action"}]}
        if not inputs.get("message"):
            return {
                "valid": False,
                "errors": [{"field": "message", "message": "message is required"}],
            }
        return {"valid": True, "errors": [], "normalized_inputs": {"message": inputs["message"]}}

    def run_backend_action(
        self,
        action_id: str,
        inputs: dict[str, Any],
        *,
        workspace: Path | None = None,
        progress: Any | None = None,
    ) -> dict[str, Any]:
        return {
            "action_id": action_id,
            "message": inputs["message"],
            "workspace": str(workspace),
            "has_progress": progress is not None,
        }


class ProviderEchoBackend(EchoBackend):
    name = "provider_echo"


class FakeColmapBackend(StubBackend):
    name = "fake_colmap"
    version = "4.1-test"
    vendor = "tests"

    def list_colmap_commands(self) -> list[str]:
        return ["version", "feature_extractor"]

    def colmap_command_schema(self, command: str) -> dict[str, Any]:
        if command == "version":
            return {
                "command": "version",
                "available": True,
                "schema_source": "test",
                "options": [],
                "option_count": 0,
            }
        if command == "feature_extractor":
            return {
                "command": "feature_extractor",
                "available": True,
                "schema_source": "test",
                "options": [
                    {
                        "name": "database_path",
                        "flags": ["--database_path"],
                        "takes_value": True,
                        "type": "string",
                        "required": True,
                        "schema": {"type": "string"},
                    },
                    {
                        "name": "SiftExtraction.max_num_features",
                        "flags": ["--SiftExtraction.max_num_features"],
                        "takes_value": True,
                        "type": "integer",
                        "schema": {"type": "integer"},
                    },
                    {
                        "name": "ImageReader.single_camera",
                        "flags": ["--ImageReader.single_camera"],
                        "takes_value": True,
                        "type": "boolean",
                        "schema": {"type": "boolean"},
                    },
                ],
                "option_count": 3,
            }
        raise AssertionError(command)

    def run_colmap_command(
        self,
        command: str,
        *,
        options: dict[str, Any] | None = None,
        positional: list[str] | None = None,
    ) -> dict[str, Any]:
        return {
            "command": command,
            "returncode": 0,
            "stdout": "ok",
            "stderr": "",
            "options": options or {},
            "positional": positional or [],
        }


class GenericColmapBackend(FakeColmapBackend):
    def list_backend_actions(self, *, include_schemas: bool = False) -> list[dict[str, Any]]:
        input_schema = (
            {"type": "object", "properties": {"from_generic": {"type": "boolean"}}}
            if include_schemas
            else None
        )
        return [
            {
                "action_id": "colmap.feature_extractor",
                "display_name": "Native feature extractor",
                "stability": "backend_extension",
                "side_effects": "write",
                "required_capabilities": [],
                "input_schema": input_schema,
                "metadata": {"include_schemas": include_schemas},
            }
        ]


async def _client_for_backend(
    monkeypatch: pytest.MonkeyPatch,
    backend_name: str,
    backend_cls: type[StubBackend],
) -> AsyncClient:
    monkeypatch.setenv("SFMAPI_BACKEND", backend_name)
    register_backend(backend_name, backend_cls)
    reset_settings_for_tests()
    reset_capabilities_cache()
    from app.main import create_app

    return AsyncClient(
        transport=ASGITransport(app=create_app()),
        base_url="http://testserver",
    )


async def test_backend_action_catalog_validate_and_run_job(
    db_setup: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with await _client_for_backend(monkeypatch, "echo", EchoBackend) as client:
        project = (await client.post("/v1/projects", json={"name": "actions"})).json()

        backend = await client.get("/v1/backend")
        assert backend.status_code == 200
        assert backend.json()["action_count"] == 1

        actions = await client.get("/v1/backend/actions")
        assert actions.status_code == 200
        assert actions.json()["items"][0]["action_id"] == "echo.echo"
        assert actions.json()["items"][0]["input_schema"] is None

        actions_with_schema = await client.get("/v1/backend/actions?include_schemas=true")
        assert actions_with_schema.status_code == 200
        assert actions_with_schema.json()["items"][0]["input_schema"]["properties"]["message"]

        # :validate is shallow-by-contract (identical to the C++ tier): a
        # known action returns valid:true + echoed inputs for any envelope.
        shallow = await client.post(
            "/v1/backend/actions/echo.echo:validate",
            json={"inputs": {}},
        )
        assert shallow.status_code == 200
        assert shallow.json()["valid"] is True
        assert shallow.json()["normalized_inputs"] == {}

        # :submit is shallow (parity with the C++ port): it creates the job
        # without input-semantic validation. The missing required "message"
        # is caught at execution by the bridge worker, so the submit is
        # accepted (202) and the JOB fails.
        submitted_bad = await client.post(
            "/v1/backend/actions/echo.echo:run",
            json={"project_id": project["project_id"], "inputs": {}},
        )
        assert submitted_bad.status_code == 202, submitted_bad.text
        bad_job = (await client.get(f"/v1/jobs/{submitted_bad.json()['job_id']}")).json()
        assert bad_job["status"] == "failed", bad_job

        accepted = await client.post(
            "/v1/backend/actions/echo.echo:run",
            json={"project_id": project["project_id"], "inputs": {"message": "hello"}},
        )
        assert accepted.status_code == 202, accepted.text
        assert accepted.headers["Location"].startswith("/v1/jobs/")
        payload = accepted.json()
        assert payload["action_id"] == "echo.echo"
        assert payload["backend"] == "echo"

        detail = await client.get(f"/v1/jobs/{payload['job_id']}")
        assert detail.status_code == 200
        job = detail.json()
        assert job["status"] == "succeeded"
        output = job["tasks"][0]["outputs_ref"]
        assert output["action_id"] == "echo.echo"
        assert output["result"]["message"] == "hello"
        assert output["result"]["has_progress"] is True


async def test_backend_action_api_can_target_provider_alias(
    db_setup: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SFMAPI_BACKEND", "echo")
    register_backend("echo", EchoBackend)
    register_backend("provider_echo", ProviderEchoBackend, providers=["provider.echo"])
    reset_settings_for_tests()
    reset_capabilities_cache()
    from app.main import create_app

    async with AsyncClient(
        transport=ASGITransport(app=create_app()),
        base_url="http://testserver",
    ) as client:
        project = (await client.post("/v1/projects", json={"name": "provider-actions"})).json()

        actions = await client.get("/v1/backend/actions?provider=provider.echo")
        assert actions.status_code == 200, actions.text
        assert actions.json()["items"][0]["backend"] == "provider_echo"

        valid = await client.post(
            "/v1/backend/actions/echo.echo:validate",
            json={"provider": "provider.echo", "inputs": {"message": "hello"}},
        )
        assert valid.status_code == 200, valid.text
        assert valid.json()["valid"] is True

        accepted = await client.post(
            "/v1/backend/actions/echo.echo:run",
            json={
                "project_id": project["project_id"],
                "provider": "provider.echo",
                "inputs": {"message": "from-provider"},
            },
        )
        assert accepted.status_code == 202, accepted.text
        assert accepted.json()["backend"] == "provider_echo"
        assert accepted.json()["provider"] == "provider.echo"
        job = (await client.get(f"/v1/jobs/{accepted.json()['job_id']}")).json()
        output = job["tasks"][0]["outputs_ref"]
        assert output["backend"] == "provider_echo"
        assert output["result"]["message"] == "from-provider"


async def test_backend_action_validate_uses_project_routing_profile(
    db_setup: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import backend_action_service
    from sfm_hub.state import PluginState, RoutingProfile

    class EmptyBackend(StubBackend):
        name = "empty"

        def list_backend_actions(self) -> list[dict[str, Any]]:
            return []

    monkeypatch.setenv("SFMAPI_BACKEND", "empty")
    register_backend("empty", EmptyBackend, providers=["bad.provider"])
    register_backend("echo", EchoBackend, providers=["good.provider"])
    reset_settings_for_tests()
    reset_capabilities_cache()

    rows = [
        SimpleNamespace(
            plugin_id="bad",
            provider=SimpleNamespace(
                provider_id="bad.provider",
                backend_actions=["echo.*"],
            ),
        ),
        SimpleNamespace(
            plugin_id="good",
            provider=SimpleNamespace(
                provider_id="good.provider",
                backend_actions=["echo.*"],
            ),
        ),
    ]
    state = PluginState(
        profiles={
            "default": RoutingProfile(
                name="default",
                routes={"actions": ["bad.provider"]},
            ),
            "project": RoutingProfile(
                name="project",
                routes={"actions": ["good.provider"]},
            ),
        },
        default_profile="default",
        project_profiles={"project-1": "project"},
    )
    monkeypatch.setattr(backend_action_service, "provider_records", lambda state=None: rows)
    monkeypatch.setattr(backend_action_service, "load_state", lambda: state)

    from app.main import create_app

    async with AsyncClient(
        transport=ASGITransport(app=create_app()),
        base_url="http://testserver",
    ) as client:
        project = (await client.post("/v1/projects", json={"name": "route-project"})).json()
        state.project_profiles = {project["project_id"]: "project"}

        routed = await client.post(
            "/v1/backend/actions/echo.echo:validate",
            json={"project_id": project["project_id"], "inputs": {"message": "hello"}},
        )
        assert routed.status_code == 200, routed.text

        missing = await client.post(
            "/v1/backend/actions/echo.echo:validate",
            json={
                "project_id": "01H00000000000000000000000",
                "inputs": {"message": "hello"},
            },
        )
        assert missing.status_code == 404, missing.text

        default = await client.post(
            "/v1/backend/actions/echo.echo:validate",
            json={"inputs": {"message": "hello"}},
        )
        assert default.status_code == 404, default.text


def test_backend_action_routing_uses_actions_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import backend_action_service as service
    from sfm_hub.models import ProviderManifest
    from sfm_hub.routing import ProviderRecord
    from sfm_hub.state import RoutingProfile, load_state, save_state

    rows = [
        ProviderRecord(
            plugin_id="alpha_plugin",
            installed=True,
            enabled=True,
            runtime_modes=["uv"],
            provider=ProviderManifest(
                provider_id="alpha",
                display_name="alpha",
                backend_actions=["echo.*"],
            ),
        ),
        ProviderRecord(
            plugin_id="beta_plugin",
            installed=True,
            enabled=True,
            runtime_modes=["uv"],
            provider=ProviderManifest(
                provider_id="beta",
                display_name="beta",
                backend_actions=["echo.*"],
            ),
        ),
    ]
    monkeypatch.setattr(service, "provider_records", lambda **kwargs: rows)
    monkeypatch.setattr(
        service,
        "list_backend_providers",
        lambda: ["alpha@alpha_plugin", "beta@beta_plugin"],
    )
    state = load_state()
    state.profiles["prefer-beta"] = RoutingProfile(
        name="prefer-beta",
        routes={"actions": ["beta@beta_plugin"]},
    )
    state.default_profile = "prefer-beta"
    save_state(state)

    assert service._resolve_action_provider("echo.echo", None) == "beta@beta_plugin"


def test_backend_action_routing_uses_single_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import backend_action_service as service
    from sfm_hub.models import ProviderManifest
    from sfm_hub.routing import ProviderRecord

    rows = [
        ProviderRecord(
            plugin_id="alpha_plugin",
            installed=True,
            enabled=True,
            runtime_modes=["uv"],
            provider=ProviderManifest(
                provider_id="alpha",
                display_name="alpha",
                backend_actions=["echo.*"],
            ),
        )
    ]
    monkeypatch.setattr(service, "provider_records", lambda **kwargs: rows)
    monkeypatch.setattr(service, "list_backend_providers", lambda: ["alpha"])

    assert service._resolve_action_provider("echo.echo", None) == "alpha"


def test_backend_action_routing_rejects_ambiguous_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import backend_action_service as service
    from sfm_hub.models import ProviderManifest
    from sfm_hub.routing import ProviderAmbiguityError, ProviderRecord

    rows = [
        ProviderRecord(
            plugin_id="alpha_plugin",
            installed=True,
            enabled=True,
            runtime_modes=["uv"],
            provider=ProviderManifest(
                provider_id="alpha",
                display_name="alpha",
                backend_actions=["echo.*"],
            ),
        ),
        ProviderRecord(
            plugin_id="beta_plugin",
            installed=True,
            enabled=True,
            runtime_modes=["uv"],
            provider=ProviderManifest(
                provider_id="beta",
                display_name="beta",
                backend_actions=["echo.*"],
            ),
        ),
    ]
    monkeypatch.setattr(service, "provider_records", lambda **kwargs: rows)
    monkeypatch.setattr(
        service,
        "list_backend_providers",
        lambda: ["alpha", "beta"],
    )

    with pytest.raises(ProviderAmbiguityError, match="alpha@alpha_plugin"):
        service._resolve_action_provider("echo.echo", None)


def test_backend_catalog_rejects_ambiguous_bare_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import backend_action_service as service
    from sfm_hub.models import ProviderManifest
    from sfm_hub.routing import ProviderAmbiguityError, ProviderRecord

    rows = [
        ProviderRecord(
            plugin_id="alpha_plugin",
            installed=True,
            enabled=True,
            runtime_modes=["uv"],
            provider=ProviderManifest(provider_id="shared", display_name="alpha"),
        ),
        ProviderRecord(
            plugin_id="beta_plugin",
            installed=True,
            enabled=True,
            runtime_modes=["uv"],
            provider=ProviderManifest(provider_id="shared", display_name="beta"),
        ),
    ]
    monkeypatch.setattr(service, "provider_records", lambda **kwargs: rows)

    with pytest.raises(ProviderAmbiguityError, match="shared@"):
        service._reject_ambiguous_external_provider("shared")


async def test_colmap_command_surface_is_adapted_as_backend_actions(
    db_setup: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with await _client_for_backend(monkeypatch, "fake_colmap", FakeColmapBackend) as client:
        project = (await client.post("/v1/projects", json={"name": "colmap-actions"})).json()

        caps = detect_capabilities()
        assert caps.supports("backend.actions")
        assert caps.supports("backend.action_schema")

        action = await client.get("/v1/backend/actions/colmap.feature_extractor")
        assert action.status_code == 200, action.text
        body = action.json()
        assert body["metadata"]["option_count"] == 3
        assert "database_path" in body["input_schema"]["properties"]
        assert "SiftExtraction.max_num_features" in body["input_schema"]["properties"]

        # :validate is shallow -- a known action returns valid:true for any
        # inputs; the unknown-option rejection happens at :run (below).
        shallow = await client.post(
            "/v1/backend/actions/colmap.feature_extractor:validate",
            json={"inputs": {"bad_option": 1}},
        )
        assert shallow.status_code == 200
        assert shallow.json()["valid"] is True

        # :submit shallow -- the unknown option is caught at execution, so
        # the submit is accepted (202) and the job fails.
        submitted_bad = await client.post(
            "/v1/backend/actions/colmap.feature_extractor:run",
            json={"project_id": project["project_id"], "inputs": {"bad_option": 1}},
        )
        assert submitted_bad.status_code == 202, submitted_bad.text
        bad_job = (await client.get(f"/v1/jobs/{submitted_bad.json()['job_id']}")).json()
        assert bad_job["status"] == "failed", bad_job

        accepted = await client.post(
            "/v1/backend/actions/colmap.feature_extractor:run",
            json={
                "project_id": project["project_id"],
                "inputs": {
                    "database_path": "database.db",
                    "SiftExtraction.max_num_features": 1024,
                },
            },
        )
        assert accepted.status_code == 202, accepted.text
        job = (await client.get(f"/v1/jobs/{accepted.json()['job_id']}")).json()
        output = job["tasks"][0]["outputs_ref"]
        assert output["backend"] == "fake_colmap"
        assert output["result"]["command"] == "feature_extractor"
        assert output["result"]["options"]["database_path"] == "database.db"


def test_explicit_backend_action_descriptor_wins_over_colmap_compat_adapter() -> None:
    from app.adapters.backend_actions import (
        assert_backend_action_contract,
        get_backend_action,
        list_backend_actions,
    )

    backend = GenericColmapBackend()

    action = get_backend_action("colmap.feature_extractor", backend)
    assert action["display_name"] == "Native feature extractor"
    assert action["required_capabilities"] == []
    assert_backend_action_contract(backend)

    actions = [
        item
        for item in list_backend_actions(backend, include_schemas=False)
        if item["action_id"] == "colmap.feature_extractor"
    ]
    assert len(actions) == 1
    assert actions[0]["input_schema"] is None

    actions_with_schema = [
        item
        for item in list_backend_actions(backend, include_schemas=True)
        if item["action_id"] == "colmap.feature_extractor"
    ]
    assert len(actions_with_schema) == 1
    assert actions_with_schema[0]["metadata"]["include_schemas"] is True
    assert "from_generic" in actions_with_schema[0]["input_schema"]["properties"]
