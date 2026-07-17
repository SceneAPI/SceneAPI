"""Async engine + session factory.

We deliberately use `asyncpg`/`aiosqlite` so the FastAPI request path is
fully async. Workers may use the sync session if needed (not yet).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from sfmapi.server.core.config import Settings, get_settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine(settings: Settings | None = None) -> AsyncEngine:
    global _engine, _session_factory
    if _engine is None:
        s = settings or get_settings()
        kwargs: dict = {"future": True, "pool_pre_ping": True}
        # In-memory SQLite needs StaticPool so every session shares one
        # connection — otherwise each connection sees an empty DB.
        if s.db_url.startswith("sqlite") and ":memory:" in s.db_url:
            from sqlalchemy.pool import StaticPool

            kwargs["poolclass"] = StaticPool
            kwargs["connect_args"] = {"uri": True} if "uri=true" in s.db_url.lower() else {}
            kwargs.pop("pool_pre_ping", None)
        _engine = create_async_engine(s.db_url, **kwargs)
        _session_factory = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        get_engine()
    assert _session_factory is not None
    return _session_factory


async def reset_engine_for_tests(settings: Settings | None = None) -> AsyncEngine:
    """Test helper — dispose existing engine and rebuild from settings."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None
    return get_engine(settings)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
