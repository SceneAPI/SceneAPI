"""Portable dataset-scoped stage routes.

Decomposed pipeline stages that operate on a dataset's feature
database:

  - ``:buildVocabTree``  — build a retrieval index (``index.vocab_tree``)
  - ``:configureRig``    — declare/calibrate a rig (``rigs.configure``)
  - ``:estimateTwoView`` — two-view geometry (``geometry.two_view``)

Each enqueues a single Task and returns the canonical 202 envelope;
clients follow ``Location`` to ``GET /v1/jobs/{job_id}`` for status.
The resolved ``provider`` is echoed on the 202.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from sceneapi.server.api.v1._helpers import accepted_response
from sceneapi.server.core.tenancy import current_tenant
from sceneapi.server.db.session import get_db
from sceneapi.server.schemas.api.jobs import JobAcceptedResponse
from sceneapi.server.schemas.api.stages import RigConfigSpec, TwoViewSpec, VocabTreeSpec
from sceneapi.server.services import sfm_stage_service

router = APIRouter(prefix="/datasets/{dataset_id}", tags=["stages"])


@router.post(
    ":buildVocabTree", status_code=status.HTTP_202_ACCEPTED, response_model=JobAcceptedResponse
)
async def build_vocab_tree(
    dataset_id: str,
    body: VocabTreeSpec,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Build a reusable vocabulary-tree retrieval index for the dataset."""
    spec = body.model_dump(mode="json")
    job_id, _tasks = await sfm_stage_service.submit_build_vocab_tree(
        session, tenant_id=tenant_id, dataset_id=dataset_id, spec=spec
    )
    return accepted_response(
        JobAcceptedResponse(job_id=job_id, dataset_id=dataset_id, provider=spec.get("provider"))
    )


@router.post(
    ":configureRig", status_code=status.HTTP_202_ACCEPTED, response_model=JobAcceptedResponse
)
async def configure_rig(
    dataset_id: str,
    body: RigConfigSpec,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Declare or calibrate a multi-camera rig over the dataset's feature DB."""
    spec = body.model_dump(mode="json")
    job_id, _tasks = await sfm_stage_service.submit_configure_rig(
        session, tenant_id=tenant_id, dataset_id=dataset_id, spec=spec
    )
    return accepted_response(
        JobAcceptedResponse(job_id=job_id, dataset_id=dataset_id, provider=spec.get("provider"))
    )


@router.post(
    ":estimateTwoView", status_code=status.HTTP_202_ACCEPTED, response_model=JobAcceptedResponse
)
async def estimate_two_view(
    dataset_id: str,
    body: TwoViewSpec,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Estimate two-view geometry (E/F/H + relative pose) for image pairs."""
    spec = body.model_dump(mode="json")
    job_id, _tasks = await sfm_stage_service.submit_estimate_two_view(
        session, tenant_id=tenant_id, dataset_id=dataset_id, spec=spec
    )
    return accepted_response(
        JobAcceptedResponse(job_id=job_id, dataset_id=dataset_id, provider=spec.get("provider"))
    )
