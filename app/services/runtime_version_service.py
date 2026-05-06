"""runtime_version row management."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings, runtime_version_tuple
from app.db.models import RuntimeVersion


async def ensure_runtime_version(
    session: AsyncSession, settings: Settings | None = None
) -> RuntimeVersion:
    s = settings or get_settings()
    rv_id, seed = runtime_version_tuple(s)
    stmt = select(RuntimeVersion).where(
        RuntimeVersion.runtime_version_id == rv_id,
        RuntimeVersion.seed == seed,
    )
    result = await session.execute(stmt)
    row = result.scalar_one_or_none()
    if row is not None:
        return row
    row = RuntimeVersion(runtime_version_id=rv_id, seed=seed)
    session.add(row)
    await session.flush()
    return row
