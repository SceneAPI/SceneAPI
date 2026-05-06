"""Shared pytest fixtures."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture(autouse=True)
def _isolate_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Each test gets its own workspace + sqlite db."""
    ws = tmp_path / "ws"
    ws.mkdir()
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("SFMAPI_DB_URL", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("SFMAPI_WORKSPACE_ROOT", str(ws))
    monkeypatch.setenv("SFMAPI_BLOB_ROOT", str(ws / "_blobs"))
    monkeypatch.setenv("SFMAPI_S3_CACHE_ROOT", str(ws / "_cache" / "s3"))
    monkeypatch.setenv("SFMAPI_AUTH_MODE", "none")
    monkeypatch.setenv("SFMAPI_DEFAULT_TENANT", "default")
    monkeypatch.setenv("SFMAPI_LEASE_TTL_SECONDS", "5")
    # Avoid touching Redis in tests; route every task through inline runner.
    monkeypatch.setenv("SFMAPI_INLINE_TASKS", "true")
    # sfmapi ships no concrete backend; register a test stub so
    # `get_backend()` resolves.
    monkeypatch.setenv("SFMAPI_BACKEND", "stub")

    from app.adapters.registry import _REGISTRY, register_backend
    from app.adapters.stub_backend import StubBackend
    from app.core import config as config_mod
    from app.core.capabilities import reset_capabilities_cache
    from app.db import session as session_mod

    config_mod._settings = None
    session_mod._engine = None
    session_mod._session_factory = None
    saved_registry = dict(_REGISTRY)
    register_backend("stub", StubBackend)
    reset_capabilities_cache()
    yield ws
    _REGISTRY.clear()
    _REGISTRY.update(saved_registry)
    reset_capabilities_cache()


@pytest_asyncio.fixture()
async def db_setup() -> AsyncIterator[None]:
    """Create the schema for the per-test sqlite db."""
    # IMPORTANT: import models BEFORE touching Base.metadata so that all
    # ORM classes register their tables. Without this, a fresh process
    # whose first test never imports `app.db.models` (directly or
    # transitively) ends up with empty metadata, and `create_all`
    # silently produces a database with zero tables.
    from app.db import models  # noqa: F401  (registers tables)
    from app.db.base import Base
    from app.db.session import get_engine

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


@pytest_asyncio.fixture()
async def session(db_setup: None) -> AsyncIterator[AsyncSession]:
    from app.db.session import get_session_factory

    factory = get_session_factory()
    async with factory() as s:
        yield s


@pytest_asyncio.fixture()
async def client(db_setup: None) -> AsyncIterator[AsyncClient]:
    from app.main import create_app

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


@pytest.fixture
def event_loop_policy() -> Iterator[asyncio.AbstractEventLoopPolicy]:
    return asyncio.DefaultEventLoopPolicy()
