"""Integration tests for ``POST /v1/oneshot/localize``. Cover the
route + auth + recon-lookup + sparse-dir resolution. The
pycolmap-bound backend call itself is covered by the
``needs_pycolmap`` e2e tests."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.integration


async def test_oneshot_localize_route_registered(db_setup) -> None:
    """Route exists and reaches the recon-lookup. A non-existent
    recon_id returns 404 from the standard tenancy machinery."""
    _ = db_setup
    from sceneapi.server.main import create_app

    app = create_app()
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        r = await client.post(
            "/v1/oneshot/localize?recon_id=01HZNOTAREALRECON00000000Z",
            content=b"\xff\xd8\xff\xe0fake-jpeg",
            headers={"Content-Type": "image/jpeg"},
        )
        assert r.status_code == 404, r.text
        body = r.json()
        # RFC7807 envelope from sceneapi/server/core/errors.py.
        assert body.get("status") == 404
        assert "not found" in str(body.get("detail", "")).lower()


async def test_oneshot_localize_rejects_oversized_body(monkeypatch) -> None:
    """Quota cap kicks in before recon lookup."""
    monkeypatch.setenv("SCENEAPI_ONESHOT_MAX_REQUEST_BYTES", "16")
    from sceneapi.server.core.config import reset_settings_for_tests

    reset_settings_for_tests()
    try:
        from sceneapi.server.main import create_app

        app = create_app()
        async with (
            app.router.lifespan_context(app),
            AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
        ):
            r = await client.post(
                "/v1/oneshot/localize?recon_id=01HZTESTRECON0000000000000",
                content=b"\xff\xd8\xff\xe0" + b"x" * 28,
                headers={"Content-Type": "image/jpeg"},
            )
            assert r.status_code == 429, r.text
    finally:
        reset_settings_for_tests()


async def test_oneshot_localize_requires_recon_id() -> None:
    """Missing the required ``recon_id`` query parameter → 422."""
    from sceneapi.server.main import create_app

    app = create_app()
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        r = await client.post(
            "/v1/oneshot/localize",
            content=b"\xff\xd8\xff\xe0fake-jpeg",
            headers={"Content-Type": "image/jpeg"},
        )
        assert r.status_code == 422, r.text
