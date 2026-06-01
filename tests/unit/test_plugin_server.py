"""The reusable plugin HTTP service adapter (sfmapi-plugin-http-v1 server)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.adapters.stub_backend import StubBackend
from app.plugin_server import (
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


def test_execute_roundtrips_the_protocol_envelope() -> None:
    r = _client().post(
        "/execute",
        json={
            "protocol": PROTOCOL,
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


def test_execute_rejects_wrong_protocol() -> None:
    r = _client().post("/execute", json={"protocol": "nope", "task_kind": "x"})
    assert r.status_code == 400
    assert r.json()["error"] == "protocol_mismatch"


def test_execute_reports_missing_field() -> None:
    r = _client().post("/execute", json={"protocol": PROTOCOL})  # no task_kind
    assert r.status_code == 400
    assert r.json()["error"] == "missing_field"


def test_protocol_compatibility_is_major_version_based() -> None:
    assert protocol_compatible("1.0")
    assert protocol_compatible("1.7")
    assert not protocol_compatible("2.0")  # a future ...-http-v2
    assert not protocol_compatible("")


def test_capabilities_hash_is_order_independent() -> None:
    assert capabilities_hash(["b", "a"]) == capabilities_hash(["a", "b"])
