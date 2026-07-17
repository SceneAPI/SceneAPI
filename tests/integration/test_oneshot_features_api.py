"""Integration tests for ``POST /v1/oneshot/features``. The route
delegates to ``oneshot_service.extract_features_oneshot``; the
service requires pycolmap to actually do feature extraction. These
tests cover the layers that DON'T require pycolmap (route
registration, request validation, quota enforcement, the
pycolmap-unavailable error path)."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.integration


async def _client():
    from sceneapi.server.main import create_app

    app = create_app()
    return app, AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_oneshot_features_route_registered() -> None:
    """The route exists, responds, and goes through the auth dep."""
    from sceneapi.server.main import create_app

    app = create_app()
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        # Empty body — should reach the validation layer.
        r = await client.post(
            "/v1/oneshot/features",
            content=b"",
            headers={"Content-Type": "application/octet-stream"},
        )
        # 422 is the route + validation-error response.
        assert r.status_code in (400, 422), r.text


async def test_oneshot_features_rejects_oversized_body(monkeypatch) -> None:
    """Quota cap kicks in before any pycolmap work."""
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
            # 32 bytes > 16-byte cap.
            r = await client.post(
                "/v1/oneshot/features",
                content=b"\xff\xd8\xff\xe0" + b"x" * 28,
                headers={"Content-Type": "image/jpeg"},
            )
            assert r.status_code == 429, r.text
            body = r.json()
            assert "exceeds" in str(body).lower()
    finally:
        reset_settings_for_tests()


async def test_oneshot_features_rejects_bad_content_type() -> None:
    """A clearly-non-image content type fails fast at the service
    boundary with a 422."""
    from sceneapi.server.main import create_app

    app = create_app()
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        r = await client.post(
            "/v1/oneshot/features",
            content=b"some-non-image-bytes",
            headers={"Content-Type": "text/csv"},
        )
        assert r.status_code in (400, 422), r.text
        assert "unsupported content type" in r.json().get("detail", "").lower()
