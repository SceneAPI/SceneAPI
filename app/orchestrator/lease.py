"""Lease helpers — works on SQLite and Postgres.

Postgres has `SELECT ... FOR UPDATE SKIP LOCKED`; SQLite does not. We use
the portable subset: an UPDATE that increments lease only when the
existing lease has expired, then check `rowcount`. This works on both
engines without dialect branches.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, update
from sqlalchemy.ext.asyncio import AsyncSession


def now_utc() -> datetime:
    return datetime.now(UTC)


async def try_acquire_lease(
    session: AsyncSession,
    *,
    table,
    pk_col,
    lease_col,
    worker_col,
    pk_value: str,
    worker_id: str,
    ttl_seconds: int,
    extra_where: tuple[Any, ...] = (),
) -> bool:
    new_expiry = now_utc() + timedelta(seconds=ttl_seconds)
    stmt = (
        update(table)
        .where(
            pk_col == pk_value,
            or_(lease_col.is_(None), lease_col < now_utc()),
            *extra_where,
        )
        .values({lease_col.key: new_expiry, worker_col.key: worker_id})
    )
    result = await session.execute(stmt)
    return result.rowcount == 1


async def refresh_lease(
    session: AsyncSession,
    *,
    table,
    pk_col,
    lease_col,
    worker_col,
    pk_value: str,
    worker_id: str,
    ttl_seconds: int,
) -> bool:
    new_expiry = now_utc() + timedelta(seconds=ttl_seconds)
    stmt = (
        update(table)
        .where(pk_col == pk_value, worker_col == worker_id)
        .values({lease_col.key: new_expiry})
    )
    result = await session.execute(stmt)
    return result.rowcount == 1
