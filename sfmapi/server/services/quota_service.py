"""Quota enforcement hooks (NOOP unless `auth_mode=api_key`)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sfmapi.server.core.config import get_settings
from sfmapi.server.core.errors import QuotaExceededError
from sfmapi.server.db.models import GpuUsage, TenantQuota


async def get_or_create_quota(session: AsyncSession, *, tenant_id: str) -> TenantQuota:
    q = await session.get(TenantQuota, tenant_id)
    if q is None:
        q = TenantQuota(tenant_id=tenant_id)
        session.add(q)
        await session.flush()
    return q


async def check_storage(session: AsyncSession, *, tenant_id: str, additional: int) -> None:
    if get_settings().auth_mode == "none":
        return
    q = await get_or_create_quota(session, tenant_id=tenant_id)
    if q.storage_bytes_max is None:
        return
    if q.storage_bytes_used + additional > q.storage_bytes_max:
        raise QuotaExceededError(
            f"storage quota: used={q.storage_bytes_used}, max={q.storage_bytes_max}"
        )


async def check_gpu_seconds(session: AsyncSession, *, tenant_id: str) -> None:
    if get_settings().auth_mode == "none":
        return
    q = await get_or_create_quota(session, tenant_id=tenant_id)
    if q.gpu_seconds_per_day_max is None:
        return
    since = datetime.now(UTC) - timedelta(days=1)
    used = (
        await session.execute(
            select(func.coalesce(func.sum(GpuUsage.gpu_seconds), 0)).where(
                GpuUsage.tenant_id == tenant_id, GpuUsage.started_at >= since
            )
        )
    ).scalar_one()
    if int(used) >= q.gpu_seconds_per_day_max:
        raise QuotaExceededError(f"gpu seconds quota: used={used}, max={q.gpu_seconds_per_day_max}")
