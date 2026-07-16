"""Single-image localization against a reconstruction.

``POST /v1/reconstructions/{recon_id}/localize`` enqueues a worker job
that runs SIFT on the query image and calls
``pycolmap.localize_from_memory``. The job's task carries a
:class:`~app.schemas.api.scene.LocalizationResult`-shaped payload in
its ``outputs_ref`` once finished — clients poll
``GET /v1/jobs/{job_id}`` for completion and read the result there.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1._helpers import accepted_response
from app.core.tenancy import current_tenant
from app.db.session import get_db
from app.schemas.api.jobs import JobAcceptedResponse
from app.schemas.api.stages import GeoregisterRequest
from app.schemas.pipeline_spec import (
    PROVIDER_SELECTOR_MAX_LENGTH,
    PROVIDER_SELECTOR_PATTERN,
)
from app.services import sfm_stage_service

router = APIRouter(prefix="/reconstructions/{recon_id}", tags=["localize"])


class LocalizationRequest(BaseModel):
    """Request body for ``POST /v1/reconstructions/{rid}/localize``."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    blob_sha: str = Field(..., min_length=64, max_length=64)
    sift: dict | None = None
    provider: str | None = Field(
        default=None,
        min_length=1,
        max_length=PROVIDER_SELECTOR_MAX_LENGTH,
        pattern=PROVIDER_SELECTOR_PATTERN,
        description="Optional provider id to execute this localize job.",
    )


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
    spec: dict = {}
    if body.sift:
        spec["sift"] = body.sift
    if body.provider is not None:
        spec["provider"] = body.provider
    job_id, _tasks = await sfm_stage_service.submit_localize(
        session,
        tenant_id=tenant_id,
        recon_id=recon_id,
        blob_sha=body.blob_sha,
        spec=spec,
    )
    # submit_localize resolves provider in place on spec — echo what
    # routing actually chose, not just what the request pinned.
    return accepted_response(
        JobAcceptedResponse(job_id=job_id, recon_id=recon_id, provider=spec.get("provider"))
    )


@router.post(
    "/georegister",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobAcceptedResponse,
)
async def georegister(
    recon_id: str,
    body: GeoregisterRequest,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Georegister the reconstruction.

    ``mode=sim3`` (default) applies the supplied ``sim3`` transform;
    ``mode=gps`` solves the transform from georeferenced inputs. Either
    way the worker rewrites every camera + 3D point and seals a fresh
    snapshot, which clients read like post-mapping snapshots.
    """
    spec = body.model_dump(mode="json")
    job_id, _tasks = await sfm_stage_service.submit_georegister(
        session, tenant_id=tenant_id, recon_id=recon_id, spec=spec
    )
    applied_sim3 = body.sim3.model_dump(mode="json", by_alias=True) if body.sim3 else None
    return accepted_response(
        JobAcceptedResponse(
            job_id=job_id,
            recon_id=recon_id,
            applied_sim3=applied_sim3,
            provider=spec.get("provider"),
        )
    )


@router.post(
    ":to_cubemap",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobAcceptedResponse,
)
async def to_cubemap(
    recon_id: str,
    provider: str | None = Query(
        default=None,
        min_length=1,
        max_length=PROVIDER_SELECTOR_MAX_LENGTH,
        pattern=PROVIDER_SELECTOR_PATTERN,
        description="Optional provider id to execute this conversion.",
    ),
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Convert a spherical reconstruction to a 6-face cubemap rig.

    Requires the dataset to be marked ``is_spherical=true``. The
    worker re-projects each panorama into 6 faces, builds a cubemap
    rig + frames, and seals a fresh snapshot whose ``rigs.json`` and
    ``frames.json`` carry the cubemap layout.
    """
    job_id, _tasks, resolved_provider = await sfm_stage_service.submit_to_cubemap(
        session, tenant_id=tenant_id, recon_id=recon_id, provider=provider
    )
    return accepted_response(
        JobAcceptedResponse(job_id=job_id, recon_id=recon_id, provider=resolved_provider)
    )
