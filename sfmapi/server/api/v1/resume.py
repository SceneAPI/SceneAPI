"""Resume endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from sfmapi.server.core.tenancy import current_tenant
from sfmapi.server.db.session import get_db
from sfmapi.server.orchestrator.resume import resume_job
from sfmapi.server.schemas.api.common import to_out
from sfmapi.server.schemas.api.jobs import JobOut

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("/{job_id}:resume", response_model=JobOut, status_code=status.HTTP_202_ACCEPTED)
async def resume(
    job_id: str,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> JobOut:
    """Resume a previously-cancelled or failed job from its last
    sealed snapshot (AIP-136 ``:resume``). Spawns a fresh DAG that
    picks up after the saved checkpoint."""
    j = await resume_job(session, tenant_id=tenant_id, job_id=job_id)
    return to_out(JobOut, j)
