"""``sceneapi.plugin_service`` -- the public facade over ``sceneapi.server.plugin_server``."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import sceneapi.plugin_service as facade
import sceneapi.server.plugin_server as kit
from sceneapi.plugin_service import (
    PROTOCOL,
    PROTOCOL_VERSION,
    ManifestBackend,
    build_plugin_server,
)

pytestmark = pytest.mark.unit

# The manifest shape container plugins ship (see e.g. sfmapi_brush.plugin).
MANIFEST = {
    "schema_version": 1,
    "plugin_id": "radiance",
    "display_name": "Radiance",
    "capabilities": ["radiance.train", "radiance.evaluate"],
    "providers": [
        {
            "provider_id": "radiance",
            "capabilities": ["radiance.train", "radiance.metrics.psnr"],
        }
    ],
}


def _echo_executor(*, task_kind, **_kw):
    return {"status": "succeeded", "outputs": {"echo": task_kind}}


def _client() -> TestClient:
    app = build_plugin_server(
        ManifestBackend(MANIFEST, version="0.0.1"),
        plugin_id="radiance",
        package_version="0.0.1",
        executor=_echo_executor,
        runtime_info=lambda: {"provider": "radiance", "gpu_runtime_available": False},
    )
    return TestClient(app)


def test_facade_reexports_the_kit_objects() -> None:
    for name in facade.__all__:
        assert getattr(facade, name) is getattr(kit, name)
    assert set(facade.__all__) == set(kit.__all__)


def test_protocol_version_matches_app_plugin_server() -> None:
    assert PROTOCOL_VERSION == kit.PROTOCOL_VERSION
    assert PROTOCOL == kit.PROTOCOL == "sfmapi-plugin-http-v1"


def test_manifest_backend_unions_top_level_and_provider_capabilities() -> None:
    assert ManifestBackend(MANIFEST).capabilities() == [
        "radiance.evaluate",
        "radiance.metrics.psnr",
        "radiance.train",
    ]
    assert ManifestBackend(MANIFEST).name == "radiance"
    assert ManifestBackend(MANIFEST).vendor == "Radiance"


def test_manifest_server_serves_the_protocol_surface() -> None:
    client = _client()

    assert client.get("/healthz").json() == {"status": "ok"}

    version = client.get("/version").json()
    assert version["protocol"] == PROTOCOL
    assert version["protocol_version"] == PROTOCOL_VERSION
    assert version["plugin_id"] == "radiance"
    assert version["runtime"] == {"provider": "radiance", "gpu_runtime_available": False}

    capabilities = client.get("/capabilities").json()
    assert capabilities["features"] == [
        "radiance.evaluate",
        "radiance.metrics.psnr",
        "radiance.train",
    ]

    # Manifest-only plugins declare no extension catalogs.
    assert client.get("/datatypes").json()["datatypes"] == []
    assert client.get("/processors").json()["processors"] == []
    assert client.get("/pipelines").json()["pipelines"] == []


def test_manifest_server_executes_tasks_through_the_executor() -> None:
    r = _client().post(
        "/execute",
        json={
            "protocol": PROTOCOL,
            "protocol_version": PROTOCOL_VERSION,
            "task_kind": "radiance_train",
            "capability": "radiance.train",
            "provider": "radiance",
            "inputs": {},
            "spec": {},
        },
    )

    assert r.status_code == 200
    assert r.json() == {
        "protocol": PROTOCOL,
        "status": "succeeded",
        "outputs": {"echo": "radiance_train"},
    }


def test_manifest_server_validates_capability_ids() -> None:
    app = build_plugin_server(
        ManifestBackend({"plugin_id": "bad", "capabilities": ["not.a.real.capability"]}),
        plugin_id="bad",
        package_version="0.0.1",
        executor=_echo_executor,
    )
    client = TestClient(app, raise_server_exceptions=False)

    assert client.get("/capabilities").status_code == 500
