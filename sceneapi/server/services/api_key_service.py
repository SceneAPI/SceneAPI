"""API key issuance + tenant resolution."""

from __future__ import annotations

import hashlib
import secrets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sceneapi.server.core.errors import TenantViolationError
from sceneapi.server.db.models import ApiKey


def _hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def issue_key(
    session: AsyncSession, *, tenant_id: str, name: str | None = None
) -> tuple[str, ApiKey]:
    raw = "sfm_" + secrets.token_urlsafe(32)
    row = ApiKey(tenant_id=tenant_id, key_hash=_hash_key(raw), name=name)
    session.add(row)
    await session.flush()
    return raw, row


async def resolve_tenant(session: AsyncSession, *, key: str) -> str:
    h = _hash_key(key)
    result = await session.execute(select(ApiKey).where(ApiKey.key_hash == h))
    row = result.scalar_one_or_none()
    if row is None or row.revoked_at is not None:
        raise TenantViolationError("Invalid or revoked API key")
    return row.tenant_id
