"""Contract test fixtures.

Boots the app in ephemeral mode, records a small set of representative
server responses to ``tests/contract/fixtures/``, and exposes them to
tests as JSON. Tests then replay those fixtures through every SDK's
typed surface (Pydantic in Python, the openapi-types diff in TS) and
assert the structure decodes cleanly.

The fixtures are recorded fresh per session so they can never lag
behind the running server. ``TESTS_CONTRACT_REFRESH=true`` forces an
overwrite of any existing fixture file (useful when intentionally
broadening coverage).
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings, reset_settings_for_tests

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _clear_inherited_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "SFMAPI_DB_URL",
        "SFMAPI_WORKSPACE_ROOT",
        "SFMAPI_BLOB_ROOT",
        "SFMAPI_S3_CACHE_ROOT",
        "SFMAPI_INLINE_TASKS",
        "SFMAPI_QUEUE_BACKEND",
        "SFMAPI_BLOB_BACKEND",
    ):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def ephemeral_settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    _clear_inherited_env(monkeypatch)
    monkeypatch.setenv("SFMAPI_EPHEMERAL", "true")
    return reset_settings_for_tests()


@pytest.fixture
async def contract_client(ephemeral_settings: Settings) -> AsyncIterator[AsyncClient]:
    """Yield an httpx AsyncClient bound to a fresh ephemeral app
    instance with lifespan driven so the schema bootstraps."""
    from app.db import session as session_mod

    if session_mod._engine is not None:
        await session_mod._engine.dispose()
    session_mod._engine = None
    session_mod._session_factory = None

    from app.main import create_app

    app = create_app()
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://contract") as client,
    ):
        yield client


def fixture_path(name: str) -> Path:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    return FIXTURE_DIR / f"{name}.json"


def load_fixture(name: str) -> Any:
    p = fixture_path(name)
    if not p.is_file():
        raise FileNotFoundError(f"missing contract fixture: {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def save_fixture(name: str, body: Any) -> None:
    """Save a JSON-shape body to ``fixtures/<name>.json``. Idempotent —
    only writes if the content differs (or refresh is forced)."""
    p = fixture_path(name)
    payload = json.dumps(body, indent=2, sort_keys=True) + "\n"
    if p.is_file() and not os.environ.get("TESTS_CONTRACT_REFRESH"):
        existing = p.read_text(encoding="utf-8")
        if existing == payload:
            return
    p.write_text(payload, encoding="utf-8")


# ---------------------------------------------------------------------
# Live-server fixture used by every ``test_e_*`` end-to-end test.
# Earlier each test inlined ~80 lines of bootstrap + cleanup; this
# fixture extracts the harness and makes the actual test bodies short
# enough to fit on screen.
# ---------------------------------------------------------------------


@pytest.fixture
def live_ephemeral_server(monkeypatch: pytest.MonkeyPatch):
    """Boot the ephemeral app on a random localhost port via uvicorn
    in a background thread, yield ``base_url``, tear down on exit.

    Cleanup includes engine disposal + memory-blob singleton reset
    + Settings reset so subsequent tests in the same process see a
    fresh global state.
    """
    pytest.importorskip("httpx")
    import asyncio
    import threading
    import time

    import uvicorn

    from app.db import session as session_mod
    from app.storage.blobs import reset_memory_blob_store_for_tests

    _clear_inherited_env(monkeypatch)
    monkeypatch.setenv("SFMAPI_EPHEMERAL", "true")

    prior_engine = session_mod._engine

    async def _dispose(engine: object | None) -> None:
        if engine is not None:
            await engine.dispose()  # type: ignore[attr-defined]

    asyncio.run(_dispose(prior_engine))
    session_mod._engine = None
    session_mod._session_factory = None
    reset_memory_blob_store_for_tests()
    reset_settings_for_tests()
    monkeypatch.setattr(session_mod, "_engine", None, raising=False)
    monkeypatch.setattr(session_mod, "_session_factory", None, raising=False)

    from app.main import create_app

    app = create_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)

    def run() -> None:
        server.run()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    for _ in range(50):
        if server.started and server.servers:
            break
        time.sleep(0.1)
    assert server.started, "uvicorn server didn't start in time"
    sock = next(iter(server.servers[0].sockets))
    base = f"http://127.0.0.1:{sock.getsockname()[1]}"

    try:
        yield base
    finally:
        server.should_exit = True
        t.join(timeout=5.0)
        cur = session_mod._engine
        asyncio.run(_dispose(cur))
        session_mod._engine = None
        session_mod._session_factory = None
        reset_memory_blob_store_for_tests()
        reset_settings_for_tests()
