"""App boots, health endpoints respond, no heavy imports leak."""

from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.unit


def test_app_does_not_import_pycolmap_or_torch() -> None:
    # Force fresh import.
    for mod in list(sys.modules):
        if mod.startswith(("app.main", "app.api", "pycolmap", "torch", "cv2")):
            sys.modules.pop(mod, None)
    import app.main  # noqa: F401

    forbidden = {"pycolmap", "torch", "cv2", "segment_anything"}
    leaked = forbidden & set(sys.modules)
    assert leaked == set(), f"Web-layer imported heavy deps: {leaked}"


async def test_healthz_ok(client) -> None:
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_version_shape(client) -> None:
    resp = await client.get("/version")
    assert resp.status_code == 200
    body = resp.json()
    assert "sfmapi" in body
    # `backend` is the BackendVersion envelope; either populated
    # (when a backend is registered) or null. Conftest registers
    # the StubBackend so we expect populated.
    assert "backend" in body
    assert body["backend"] is not None
    assert body["backend"]["name"] == "stub"
