"""The reusable plugin HTTP service adapter (sfmapi-plugin-http-v1 server)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sfmapi.server.adapters.stub_backend import StubBackend
from sfmapi.server.plugin_server import (
    PROTOCOL,
    PROTOCOL_VERSION,
    build_plugin_server,
    capabilities_hash,
    protocol_compatible,
)

pytestmark = pytest.mark.unit


def _echo_executor(*, task_kind, **_kw):
    return {"echo": task_kind, "ran": True}


def _client() -> TestClient:
    app = build_plugin_server(
        StubBackend(),
        plugin_id="stub",
        package_version="0.0.1",
        executor=_echo_executor,
    )
    return TestClient(app)


class ExtensionBackend(StubBackend):
    def capabilities(self) -> set[str]:
        return {"radiance.train"}

    def datatypes(self) -> list[dict[str, object]]:
        return [
            {
                "type_id": "radiance_field",
                "title": "Radiance field",
                "kind": "artifact",
                "description": "Learned scene representation.",
            }
        ]

    def processors(self) -> list[dict[str, object]]:
        return [
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
                    }
                ],
                "capabilities": ["radiance.train"],
            }
        ]

    def processor_extensions(self) -> list[dict[str, object]]:
        return [
            {
                "processor_id": "map",
                "special_inputs": {
                    "radiance.prior": {"datatype": "radiance_field", "required": False}
                },
                "special_attributes": [{"name": "radiance.radiance_weight", "type": "float"}],
            }
        ]

    def pipelines(self) -> list[dict[str, object]]:
        return [
            {
                "pipeline_id": "radiance_from_sparse",
                "title": "Radiance from sparse model",
                "initial_inputs": ["sparse_model"],
                "steps": [{"ref": "train", "processor": "train"}],
            }
        ]


def _extension_client() -> TestClient:
    app = build_plugin_server(
        ExtensionBackend(),
        plugin_id="radiance",
        package_version="0.0.1",
        executor=_echo_executor,
    )
    return TestClient(app)


class InvalidExtensionBackend(StubBackend):
    def datatypes(self) -> list[dict[str, object]]:
        return [{"type_id": "Bad Type", "title": "Bad"}]


class InvalidGraphBackend(StubBackend):
    def capabilities(self) -> set[str]:
        return {"features.extract.sift"}

    def processors(self) -> list[dict[str, object]]:
        return [
            {
                "processor_id": "bad.processor",
                "title": "Bad processor",
                "consumer": {"input": {"datatype": "missing_type"}},
                "supplier": {"output": {"datatype": "sparse_model"}},
                "capabilities": ["features.extract.sift"],
            }
        ]


class InvalidCapabilityBackend(StubBackend):
    def capabilities(self) -> set[str]:
        return {"not.a.real.capability"}


class ActionBackend(StubBackend):
    def list_backend_actions(self) -> list[dict[str, object]]:
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

    def validate_backend_action(
        self, action_id: str, inputs: dict[str, object]
    ) -> dict[str, object]:
        return {
            "action_id": action_id,
            "valid": True,
            "errors": [],
            "normalized_inputs": dict(inputs),
        }

    def run_backend_action(self, action_id: str, inputs: dict[str, object]) -> dict[str, object]:
        return {"action_id": action_id, "inputs": dict(inputs)}


def test_healthz() -> None:
    r = _client().get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_version_carries_protocol_and_provenance() -> None:
    v = _client().get("/version").json()
    assert v["protocol"] == PROTOCOL == "sfmapi-plugin-http-v1"
    assert v["protocol_version"] == PROTOCOL_VERSION
    assert v["plugin_id"] == "stub"
    assert v["package_version"] == "0.0.1"
    assert v["backend_version"]  # non-empty
    assert len(v["capabilities_hash"]) == 64  # sha256 hex


def test_capabilities_lists_the_backend_features() -> None:
    c = _client().get("/capabilities").json()
    assert c["schema_version"] == 1
    assert isinstance(c["features"], list)  # bare stub may advertise none
    assert c["backend"]["name"] == "stub"


def test_capabilities_endpoint_validates_backend_capability_ids() -> None:
    app = build_plugin_server(
        InvalidCapabilityBackend(),
        plugin_id="bad-capabilities",
        package_version="0.0.1",
        executor=_echo_executor,
    )
    client = TestClient(app, raise_server_exceptions=False)

    assert client.get("/capabilities").status_code == 500


def test_extension_catalog_endpoints_default_to_empty_lists() -> None:
    client = _client()

    assert client.get("/datatypes").json() == {
        "schema_version": 1,
        "plugin_id": "stub",
        "datatypes": [],
    }
    assert client.get("/processors").json() == {
        "schema_version": 1,
        "plugin_id": "stub",
        "processors": [],
        "processor_extensions": [],
    }
    assert client.get("/pipelines").json() == {
        "schema_version": 1,
        "plugin_id": "stub",
        "pipelines": [],
    }


def test_extension_catalog_endpoints_return_backend_declarations() -> None:
    client = _extension_client()

    assert client.get("/datatypes").json()["datatypes"][0]["type_id"] == "radiance_field"
    processors = client.get("/processors").json()
    assert processors["processors"][0]["processor_id"] == "train"
    assert processors["processor_extensions"][0]["processor_id"] == "map"
    assert client.get("/pipelines").json()["pipelines"][0]["pipeline_id"] == (
        "radiance_from_sparse"
    )


def test_extension_catalog_endpoints_validate_backend_declarations() -> None:
    app = build_plugin_server(
        InvalidExtensionBackend(),
        plugin_id="bad",
        package_version="0.0.1",
        executor=_echo_executor,
    )
    client = TestClient(app, raise_server_exceptions=False)

    assert client.get("/datatypes").status_code == 500


def test_healthz_validates_live_extension_catalog() -> None:
    app = build_plugin_server(
        InvalidGraphBackend(),
        plugin_id="bad-graph",
        package_version="0.0.1",
        executor=_echo_executor,
    )
    client = TestClient(app, raise_server_exceptions=False)

    assert client.get("/healthz").status_code == 500


def test_extension_catalog_endpoints_validate_cross_references() -> None:
    app = build_plugin_server(
        InvalidGraphBackend(),
        plugin_id="bad-graph",
        package_version="0.0.1",
        executor=_echo_executor,
    )
    client = TestClient(app, raise_server_exceptions=False)

    assert client.get("/processors").status_code == 500


def test_action_validate_uses_backend_action_adapter() -> None:
    app = build_plugin_server(
        ActionBackend(),
        plugin_id="actions",
        package_version="0.0.1",
        executor=_echo_executor,
    )
    client = TestClient(app)

    r = client.post("/actions/echo.echo:validate", json={"message": "hello"})

    assert r.status_code == 200
    assert r.json() == {
        "valid": True,
        "errors": [],
        "normalized_inputs": {"message": "hello"},
    }


def test_execute_roundtrips_the_protocol_envelope() -> None:
    r = _client().post(
        "/execute",
        json={
            "protocol": PROTOCOL,
            "protocol_version": PROTOCOL_VERSION,
            "task_kind": "radiance_train",
            "capability": "radiance.train",
            "tenant_id": "default",
            "job_id": "j",
            "task_id": "t",
            "provider": "stub",
            "inputs": {"radiance_field_id": "rf"},
            "spec": {"max_steps": 1},
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["protocol"] == PROTOCOL  # echoed back, as the worker expects
    assert body["echo"] == "radiance_train"
    assert body["ran"] is True


def test_execute_accepts_bridge_backend_action_envelope() -> None:
    app = build_plugin_server(
        ActionBackend(),
        plugin_id="actions",
        package_version="0.0.1",
        executor=_echo_executor,
    )
    client = TestClient(app)

    r = client.post(
        "/execute",
        json={
            "protocol": PROTOCOL,
            "protocol_version": PROTOCOL_VERSION,
            "stage": "backend_action",
            "action_id": "echo.echo",
            "inputs": {"message": "hello"},
        },
    )

    assert r.status_code == 200
    assert r.json() == {
        "protocol": PROTOCOL,
        "status": "succeeded",
        "outputs": {
            "action_id": "echo.echo",
            "inputs": {"message": "hello"},
        },
    }


def test_execute_rejects_wrong_protocol() -> None:
    r = _client().post("/execute", json={"protocol": "nope", "task_kind": "x"})
    assert r.status_code == 400
    assert r.json()["error"] == "protocol_mismatch"


def test_execute_rejects_wrong_protocol_version() -> None:
    r = _client().post(
        "/execute",
        json={"protocol": PROTOCOL, "protocol_version": "2.0", "task_kind": "x"},
    )
    assert r.status_code == 400
    assert r.json()["error"] == "protocol_version_mismatch"


def test_execute_reports_missing_field() -> None:
    r = _client().post(
        "/execute",
        json={"protocol": PROTOCOL, "protocol_version": PROTOCOL_VERSION},
    )  # no task_kind
    assert r.status_code == 400
    assert r.json()["error"] == "missing_field"


def test_protocol_compatibility_is_major_version_based() -> None:
    assert protocol_compatible("1.0")
    assert protocol_compatible("1.7")
    assert not protocol_compatible("2.0")  # a future ...-http-v2
    assert not protocol_compatible("")


def test_capabilities_hash_is_order_independent() -> None:
    assert capabilities_hash(["b", "a"]) == capabilities_hash(["a", "b"])
