from __future__ import annotations

from pathlib import Path
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
    def list_backend_actions(self) -> list[dict[str, Any]]:
        return [
            {
                "action_id": "colmap.feature_extractor",
                "display_name": "Native feature extractor",
                "stability": "backend_extension",
                "side_effects": "write",
                "required_capabilities": [],
                "input_schema": {"type": "object", "properties": {}},
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
        assert (
            actions_with_schema.json()["items"][0]["input_schema"]["properties"]["message"]
        )

        invalid = await client.post(
            "/v1/backend/actions/echo.echo:validate",
            json={"inputs": {}},
        )
        assert invalid.status_code == 200
        assert invalid.json()["valid"] is False
        assert invalid.json()["errors"][0]["field"] == "message"

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

        invalid = await client.post(
            "/v1/backend/actions/colmap.feature_extractor:validate",
            json={"inputs": {"bad_option": 1}},
        )
        assert invalid.status_code == 200
        assert invalid.json()["valid"] is False
        assert "unknown option" in invalid.json()["errors"][0]["message"]

        valid = await client.post(
            "/v1/backend/actions/colmap.feature_extractor:validate",
            json={
                "inputs": {
                    "database_path": "database.db",
                    "SiftExtraction.max_num_features": 1024,
                    "ImageReader.single_camera": True,
                }
            },
        )
        assert valid.status_code == 200
        assert valid.json()["valid"] is True

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
        job = (
            await client.get(f"/v1/jobs/{accepted.json()['job_id']}")
        ).json()
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
