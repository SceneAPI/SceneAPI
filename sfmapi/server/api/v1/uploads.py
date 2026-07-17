"""Chunked upload routes — POST init / PATCH chunk / POST finalize."""

from __future__ import annotations

import re

from fastapi import APIRouter, Body, Depends, Header, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from sfmapi.server.core.errors import ValidationError
from sfmapi.server.core.tenancy import current_tenant
from sfmapi.server.db.session import get_db
from sfmapi.server.schemas.api.common import to_out
from sfmapi.server.schemas.api.uploads import UploadFinalizeRequest, UploadInit, UploadOut
from sfmapi.server.services import upload_service

router = APIRouter(prefix="/uploads", tags=["uploads"])

_CONTENT_RANGE_RE = re.compile(r"bytes (\d+)-(\d+)/(\d+|\*)")


@router.post("", response_model=UploadOut, status_code=status.HTTP_201_CREATED)
async def init(
    body: UploadInit,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> UploadOut:
    """Open a chunked-upload session.

    Reserves an ``upload_id`` for the caller to ``PATCH`` chunks into.
    ``Idempotency-Key`` (recommended) makes init replay-safe — a retry
    with the same key returns the same upload row. Returns
    :class:`UploadOut` with ``state="open"``; the row expires at
    ``expires_at`` if the client never finalizes.
    """
    u = await upload_service.init_upload(
        session,
        tenant_id=tenant_id,
        expected_size=body.expected_size,
        content_type=body.content_type,
        expected_sha=body.expected_sha,
        idempotency_key=idempotency_key,
    )
    return to_out(UploadOut, u)


@router.get("/{upload_id}", response_model=UploadOut)
async def status_route(
    upload_id: str,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> UploadOut:
    """Read the current state of an upload session.

    Useful for resuming an interrupted upload — inspect
    ``received_bytes`` and ``state`` to pick the next chunk offset.
    Returns 404 if the upload has expired or never existed.
    """
    u = await upload_service.get_upload(session, tenant_id=tenant_id, upload_id=upload_id)
    return to_out(UploadOut, u)


@router.patch(
    "/{upload_id}",
    response_model=UploadOut,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
            },
        }
    },
)
async def patch_chunk(
    upload_id: str,
    request: Request,
    content_range: str | None = Header(default=None, alias="Content-Range"),
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> UploadOut:
    """Append one chunk of bytes to the upload.

    Requires ``Content-Range: bytes <start>-<end>/<total>`` (RFC 7233);
    the body length MUST equal the byte range. 422
    ``ValidationError`` on malformed Content-Range or length mismatch.
    To retry after a lost response, first read upload status and resume
    at ``received_bytes``; already-committed offsets are rejected as
    out of order.
    """
    if not content_range:
        raise ValidationError("Content-Range header is required")
    m = _CONTENT_RANGE_RE.match(content_range)
    if not m:
        raise ValidationError(f"Malformed Content-Range: {content_range!r}")
    start, end, _total = m.group(1), m.group(2), m.group(3)
    offset = int(start)
    end_inclusive = int(end)
    expected_len = end_inclusive - offset + 1
    data = await request.body()
    if len(data) != expected_len:
        raise ValidationError(
            f"Body length {len(data)} does not match Content-Range {content_range!r}"
        )
    u = await upload_service.append_chunk(
        session, tenant_id=tenant_id, upload_id=upload_id, offset=offset, data=data
    )
    return to_out(UploadOut, u)


@router.post("/{upload_id}:finalize", response_model=UploadOut)
async def finalize(
    upload_id: str,
    payload: UploadFinalizeRequest = Body(default_factory=UploadFinalizeRequest),
    x_content_sha: str | None = Header(default=None, alias="X-Content-SHA256"),
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> UploadOut:
    """Seal an upload — verify the assembled bytes match the
    expected size and (when supplied) ``X-Content-SHA256``, then
    transition the row to ``finalized`` with the canonical
    ``blob_sha``. AIP-136 ``:finalize`` colon verb (the operation
    has side effects beyond a Standard Update)."""
    client_sha = x_content_sha or payload.content_sha
    u = await upload_service.finalize_upload(
        session,
        tenant_id=tenant_id,
        upload_id=upload_id,
        client_sha=client_sha,
    )
    return to_out(UploadOut, u)
