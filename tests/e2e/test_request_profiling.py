"""Request profiling middleware behavior."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.e2e


async def test_request_profiling_adds_server_timing_and_profile_dump(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile_dir = tmp_path / "profiles"
    monkeypatch.setenv("SCENEAPI_PROFILE_REQUESTS", "true")
    monkeypatch.setenv("SCENEAPI_PROFILE_MIN_MS", "0")
    monkeypatch.setenv("SCENEAPI_PROFILE_TOP_N", "5")
    monkeypatch.setenv("SCENEAPI_PROFILE_DIR", str(profile_dir))

    from sceneapi.server.core.config import reset_settings_for_tests
    from sceneapi.server.main import create_app

    reset_settings_for_tests()
    app = create_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/healthz")

    assert resp.status_code == 200
    assert resp.headers["Server-Timing"].startswith("app;dur=")
    dumps = list(profile_dir.glob("*.prof"))
    assert len(dumps) == 1
    assert dumps[0].stat().st_size > 0


async def test_request_profiling_does_not_dump_below_threshold(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile_dir = tmp_path / "profiles"
    monkeypatch.setenv("SCENEAPI_PROFILE_REQUESTS", "true")
    monkeypatch.setenv("SCENEAPI_PROFILE_MIN_MS", "60000")
    monkeypatch.setenv("SCENEAPI_PROFILE_DIR", str(profile_dir))

    from sceneapi.server.core.config import reset_settings_for_tests
    from sceneapi.server.main import create_app

    reset_settings_for_tests()
    app = create_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/healthz")

    assert resp.status_code == 200
    assert resp.headers["Server-Timing"].startswith("app;dur=")
    assert not list(profile_dir.glob("*.prof"))
