"""Single-image localization against a reconstruction.

``POST /v1/reconstructions/{recon_id}/localize`` enqueues a worker job
that runs SIFT on the query image and calls
``pycolmap.localize_from_memory``. The job's task carries a
:class:`~app.schemas.api.scene.LocalizationResult`-shaped payload in
its ``outputs_ref`` once finished — clients poll
``GET /v1/jobs/{job_id}`` for completion and read the result there.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1._helpers import accepted_response
from app.core.tenancy import current_tenant
from app.db.session import get_db
from app.schemas.api.jobs import JobAcceptedResponse
from app.schemas.api.scene import Sim3
from app.services import sfm_stage_service

router = APIRouter(prefix="/reconstructions/{recon_id}", tags=["localize"])


class LocalizationRequest(BaseModel):
    """Request body for ``POST /v1/reconstructions/{rid}/localize``."""

    model_config = ConfigDict(populate_by_name=True)

    blob_sha: str = Field(..., min_length=64, max_length=64)
    sift: dict | None = None


@router.post(
    "/localize",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobAcceptedResponse,
)
async def localize(
    recon_id: str,
    body: LocalizationRequest,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Localize a single query image against the reconstruction.

    The job's task carries a :class:`~app.schemas.api.scene.LocalizationResult`-
    shaped payload in its ``outputs_ref`` once finished."""
    spec = {"sift": body.sift} if body.sift else {}
    job_id, _tasks = await sfm_stage_service.submit_localize(
        session,
        tenant_id=tenant_id,
        recon_id=recon_id,
        blob_sha=body.blob_sha,
        spec=spec,
    )
    return accepted_response(JobAcceptedResponse(job_id=job_id, recon_id=recon_id))


@router.post(
    "/georegister",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobAcceptedResponse,
)
async def georegister(
    recon_id: str,
    body: Sim3,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Apply a Sim(3) similarity transform to the reconstruction.

    The worker rewrites every camera + 3D point and seals a fresh
    snapshot. Clients then read the new snapshot the same way they
    read post-mapping snapshots.
    """
    sim3 = body.model_dump(mode="json", by_alias=True)
    job_id, _tasks = await sfm_stage_service.submit_georegister(
        session, tenant_id=tenant_id, recon_id=recon_id, sim3=sim3
    )
    return accepted_response(
        JobAcceptedResponse(job_id=job_id, recon_id=recon_id, applied_sim3=sim3)
    )


@router.post(
    ":to_cubemap",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobAcceptedResponse,
)
async def to_cubemap(
    recon_id: str,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Convert a spherical reconstruction to a 6-face cubemap rig.

    Requires the dataset to be marked ``is_spherical=true``. The
    worker re-projects each panorama into 6 faces, builds a cubemap
    rig + frames, and seals a fresh snapshot whose ``rigs.json`` and
    ``frames.json`` carry the cubemap layout.
    """
    job_id, _tasks = await sfm_stage_service.submit_to_cubemap(
        session, tenant_id=tenant_id, recon_id=recon_id
    )
    return accepted_response(JobAcceptedResponse(job_id=job_id, recon_id=recon_id))
