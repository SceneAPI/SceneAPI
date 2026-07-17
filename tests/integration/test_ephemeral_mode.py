"""End-to-end smoke for SCENEAPI_EPHEMERAL=true.

Asserts that with ``ephemeral=true`` the app boots, picks the right
backends, serves a basic upload+create-project flow, and leaves no
on-disk state behind after lifespan shutdown.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from sceneapi.server.core.config import Settings, reset_settings_for_tests

pytestmark = pytest.mark.integration


@pytest.fixture
def ephemeral_settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    # Strip the conftest overrides so ephemeral defaults can win.
    for key in (
        "SCENEAPI_DB_URL",
        "SCENEAPI_WORKSPACE_ROOT",
        "SCENEAPI_BLOB_ROOT",
        "SCENEAPI_S3_CACHE_ROOT",
        "SCENEAPI_INLINE_TASKS",
        "SCENEAPI_QUEUE_BACKEND",
        "SCENEAPI_BLOB_BACKEND",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("SCENEAPI_EPHEMERAL", "true")
    return reset_settings_for_tests()


def test_ephemeral_settings_apply_overrides(ephemeral_settings: Settings) -> None:
    s = ephemeral_settings
    assert s.ephemeral is True
    assert s.blob_backend == "memory"
    assert s.queue_backend == "inline"
    assert s.inline_tasks is True
    assert ":memory:" in s.db_url
    assert s.workspace_root.exists()
    assert "ephemeral" in str(s.workspace_root).lower()


async def _reset_engine() -> None:
    from sceneapi.server.db import session as session_mod

    if session_mod._engine is not None:
        await session_mod._engine.dispose()
    session_mod._engine = None
    session_mod._session_factory = None


async def test_ephemeral_app_boot_health(ephemeral_settings: Settings) -> None:
    await _reset_engine()
    from sceneapi.server.main import create_app

    app = create_app()
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        r = await client.get("/healthz")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}

        r = await client.get("/readyz")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["checks"]["db"] == "ok"
        # Inline queue is in use → readyz should not include the queue check.
        assert "queue" not in body["checks"]


async def test_ephemeral_create_project_round_trip(ephemeral_settings: Settings) -> None:
    await _reset_engine()
    from sceneapi.server.main import create_app

    app = create_app()
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        r = await client.post("/v1/projects", json={"name": "ephemeral-demo"})
        assert r.status_code in (200, 201), r.text
        proj = r.json()
        assert proj["name"] == "ephemeral-demo"
        pid = proj["project_id"]

        r = await client.get(f"/v1/projects/{pid}")
        assert r.status_code == 200
        assert r.json()["project_id"] == pid


async def test_ephemeral_workspace_cleaned_after_shutdown(
    ephemeral_settings: Settings,
) -> None:
    await _reset_engine()
    from sceneapi.server.main import create_app

    app = create_app()
    workspace = ephemeral_settings.workspace_root
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        await client.get("/healthz")
        assert Path(workspace).exists()
    # After the lifespan exits, the tempdir is gone.
    assert not Path(workspace).exists()
