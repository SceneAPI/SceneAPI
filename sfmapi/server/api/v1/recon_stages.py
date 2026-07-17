"""Portable reconstruction-scoped stage routes.

Decomposed pipeline stages that operate on an existing reconstruction's
sparse model:

  - ``:bundleAdjust``      — bundle adjustment (``ba.*``)
  - ``:triangulate``       — re-triangulation (``triangulate.retri``)
  - ``:poseGraphOptimize`` — pose-graph optimization (``pgo.optimize``)
  - ``:export``            — portable export (``export.{format}``)
  - ``:relocalize``        — register more images (``relocalize.images``)
  - ``:undistort``         — undistort images (``image.undistort``)

Each enqueues a single Task and returns the canonical 202 envelope;
clients follow ``Location`` to ``GET /v1/jobs/{job_id}`` for status.
The resolved ``provider`` is echoed on the 202.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from sfmapi.server.api.v1._helpers import accepted_response
from sfmapi.server.core.tenancy import current_tenant
from sfmapi.server.db.session import get_db
from sfmapi.server.schemas.api.jobs import JobAcceptedResponse
from sfmapi.server.schemas.api.stages import (
    ExportSpec,
    PoseGraphSpec,
    RelocalizeSpec,
    TriangulateSpec,
    UndistortSpec,
)
from sfmapi.server.schemas.pipeline_spec import BundleAdjustmentSpec
from sfmapi.server.services import sfm_stage_service

router = APIRouter(prefix="/reconstructions/{recon_id}", tags=["stages"])


@router.post(
    ":bundleAdjust", status_code=status.HTTP_202_ACCEPTED, response_model=JobAcceptedResponse
)
async def bundle_adjust(
    recon_id: str,
    body: BundleAdjustmentSpec,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Run standalone bundle adjustment over the reconstruction.

    ``mode`` selects the algorithm + gating capability (``ba.standard``
    / ``ba.two_stage`` / ``ba.featuremetric`` / ``ba.rig``)."""
    spec = body.model_dump(mode="json")
    job_id, _tasks = await sfm_stage_service.submit_bundle_adjust(
        session, tenant_id=tenant_id, recon_id=recon_id, spec=spec
    )
    return accepted_response(
        JobAcceptedResponse(job_id=job_id, recon_id=recon_id, provider=spec.get("provider"))
    )


@router.post(
    ":triangulate", status_code=status.HTTP_202_ACCEPTED, response_model=JobAcceptedResponse
)
async def triangulate(
    recon_id: str,
    body: TriangulateSpec,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Re-triangulate the reconstruction against its feature database."""
    spec = body.model_dump(mode="json")
    job_id, _tasks = await sfm_stage_service.submit_triangulate(
        session, tenant_id=tenant_id, recon_id=recon_id, spec=spec
    )
    return accepted_response(
        JobAcceptedResponse(job_id=job_id, recon_id=recon_id, provider=spec.get("provider"))
    )


@router.post(
    ":poseGraphOptimize", status_code=status.HTTP_202_ACCEPTED, response_model=JobAcceptedResponse
)
async def pose_graph_optimize(
    recon_id: str,
    body: PoseGraphSpec,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Run pose-graph optimization over the reconstruction."""
    spec = body.model_dump(mode="json")
    job_id, _tasks = await sfm_stage_service.submit_pose_graph_optimize(
        session, tenant_id=tenant_id, recon_id=recon_id, spec=spec
    )
    return accepted_response(
        JobAcceptedResponse(job_id=job_id, recon_id=recon_id, provider=spec.get("provider"))
    )


@router.post(":export", status_code=status.HTTP_202_ACCEPTED, response_model=JobAcceptedResponse)
async def export(
    recon_id: str,
    body: ExportSpec,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Export the reconstruction's sparse model to a portable format."""
    spec = body.model_dump(mode="json")
    job_id, _tasks = await sfm_stage_service.submit_export(
        session, tenant_id=tenant_id, recon_id=recon_id, spec=spec
    )
    return accepted_response(
        JobAcceptedResponse(job_id=job_id, recon_id=recon_id, provider=spec.get("provider"))
    )


@router.post(
    ":relocalize", status_code=status.HTTP_202_ACCEPTED, response_model=JobAcceptedResponse
)
async def relocalize(
    recon_id: str,
    body: RelocalizeSpec,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Register additional images into the existing reconstruction.

    Not to be confused with ``POST /v1/reconstructions/{rid}/localize``:
    ``:relocalize`` mutates the model by registering new images, while
    ``/localize`` only queries the pose of a single image and leaves the
    reconstruction untouched."""
    spec = body.model_dump(mode="json")
    job_id, _tasks = await sfm_stage_service.submit_relocalize(
        session, tenant_id=tenant_id, recon_id=recon_id, spec=spec
    )
    return accepted_response(
        JobAcceptedResponse(job_id=job_id, recon_id=recon_id, provider=spec.get("provider"))
    )


@router.post(":undistort", status_code=status.HTTP_202_ACCEPTED, response_model=JobAcceptedResponse)
async def undistort(
    recon_id: str,
    body: UndistortSpec,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Undistort the reconstruction's images + emit adjusted intrinsics."""
    spec = body.model_dump(mode="json")
    job_id, _tasks = await sfm_stage_service.submit_undistort(
        session, tenant_id=tenant_id, recon_id=recon_id, spec=spec
    )
    return accepted_response(
        JobAcceptedResponse(job_id=job_id, recon_id=recon_id, provider=spec.get("provider"))
    )
