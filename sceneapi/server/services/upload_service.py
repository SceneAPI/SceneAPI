"""Chunked upload state management."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from sceneapi.server.core.config import Settings, get_settings
from sceneapi.server.core.errors import ConflictError, NotFoundError, ValidationError
from sceneapi.server.db.models import Blob, Upload
from sceneapi.server.services.quota_service import check_storage as check_storage_quota
from sceneapi.server.storage.blobs import TempUploadStore, get_blob_store


def _now() -> datetime:
    return datetime.now(UTC)


def _upload_expired(upload: Upload, *, now: datetime | None = None) -> bool:
    if upload.state == "finalized":
        return False
    expires_at = upload.expires_at
    if expires_at is None:
        return True
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at < (now or _now())


async def init_upload(
    session: AsyncSession,
    *,
    tenant_id: str,
    expected_size: int,
    content_type: str | None,
    expected_sha: str | None,
    idempotency_key: str | None,
    settings: Settings | None = None,
) -> Upload:
    s = settings or get_settings()
    if expected_size <= 0:
        raise ValidationError("expected_size must be > 0")
    # Quota gate at upload init: charges the tenant's storage budget
    # before bytes start landing on disk. No-op when auth_mode=none
    # (single-user deployments) — see sceneapi.server.services.quota_service.
    await check_storage_quota(session, tenant_id=tenant_id, additional=expected_size)
    await gc_expired_uploads(session)
    if idempotency_key:
        result = await session.execute(
            select(Upload).where(
                Upload.tenant_id == tenant_id,
                Upload.idempotency_key == idempotency_key,
            )
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            if _upload_expired(existing):
                raise NotFoundError(f"Upload {existing.upload_id} not found")
            return existing
    expires = _now() + timedelta(hours=s.upload_expiry_hours)
    u = Upload(
        tenant_id=tenant_id,
        idempotency_key=idempotency_key,
        expected_size=expected_size,
        content_type=content_type,
        expected_sha=expected_sha,
        state="open",
        expires_at=expires,
    )
    session.add(u)
    try:
        await session.flush()
    except IntegrityError as e:
        await session.rollback()
        raise ConflictError("Idempotency conflict on upload") from e
    return u


async def get_upload(session: AsyncSession, *, tenant_id: str, upload_id: str) -> Upload:
    result = await session.execute(
        select(Upload).where(Upload.tenant_id == tenant_id, Upload.upload_id == upload_id)
    )
    u = result.scalar_one_or_none()
    if u is None:
        raise NotFoundError(f"Upload {upload_id} not found")
    if _upload_expired(u):
        raise NotFoundError(f"Upload {upload_id} not found")
    return u


async def append_chunk(
    session: AsyncSession,
    *,
    tenant_id: str,
    upload_id: str,
    offset: int,
    data: bytes,
) -> Upload:
    u = await get_upload(session, tenant_id=tenant_id, upload_id=upload_id)
    if u.state != "open":
        raise ConflictError(f"Upload {upload_id} is not open (state={u.state})")
    if offset != u.received_bytes:
        raise ValidationError(
            f"Out-of-order chunk: expected offset {u.received_bytes}, got {offset}"
        )
    if u.received_bytes + len(data) > u.expected_size:
        raise ValidationError("Chunk exceeds expected_size")
    store = TempUploadStore()
    store.append(upload_id, offset, data)
    u.received_bytes = u.received_bytes + len(data)
    if u.received_bytes == u.expected_size:
        u.state = "received"
    return u


async def finalize_upload(
    session: AsyncSession,
    *,
    tenant_id: str,
    upload_id: str,
    client_sha: str | None = None,
) -> Upload:
    u = await get_upload(session, tenant_id=tenant_id, upload_id=upload_id)
    if u.state == "finalized":
        return u
    if u.state == "open" and u.received_bytes != u.expected_size:
        raise ConflictError(f"Upload incomplete: received {u.received_bytes}/{u.expected_size}")
    temp = TempUploadStore()
    blobs = get_blob_store()
    sha, total = temp.finalize_into(upload_id, blobs)
    if u.expected_sha and u.expected_sha != sha:
        raise ValidationError(f"Content sha mismatch: expected {u.expected_sha}, got {sha}")
    if client_sha and client_sha != sha:
        raise ValidationError(f"Content sha mismatch: client said {client_sha}, got {sha}")
    if total != u.expected_size:
        raise ValidationError(f"Final size mismatch: expected {u.expected_size}, got {total}")
    blob_row = await session.get(Blob, sha)
    if blob_row is None:
        blob_row = Blob(sha256=sha, byte_size=total, mime=u.content_type, refcount=0)
        session.add(blob_row)
        await session.flush()
    u.state = "finalized"
    u.blob_sha = sha
    return u


async def gc_expired_uploads(session: AsyncSession, *, now: datetime | None = None) -> int:
    n = now or _now()
    result = await session.execute(
        select(Upload).where(
            Upload.state != "finalized",
            or_(Upload.expires_at.is_(None), Upload.expires_at < n),
        )
    )
    rows = list(result.scalars().all())
    temp = TempUploadStore()
    for u in rows:
        temp.discard(u.upload_id)
    if rows:
        ids = [u.upload_id for u in rows]
        await session.execute(delete(Upload).where(Upload.upload_id.in_(ids)))
    return len(rows)
