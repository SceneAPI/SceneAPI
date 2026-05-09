"""SfM stage endpoints: features, matches, verify.

Each call returns 202 + a `Location` header pointing at the job. The
request body is now just the spec — image source and database paths are
derived server-side from the dataset's `source` and the cached
reconstruction.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1._helpers import accepted_response
from app.core.tenancy import current_tenant
from app.db.session import get_db
from app.schemas.api.jobs import JobAcceptedResponse
from app.schemas.pipeline_spec import FeaturesSpec, MatcherSpec, PairsSpec, VerifySpec
from app.services import sfm_stage_service

router = APIRouter(prefix="/datasets/{dataset_id}", tags=["sfm-stages"])


class _StageReqBase(BaseModel):
    """Strict request envelope for SfM stage submissions.

    Extras 422 — typo'd field names should fail loud rather than
    silently ship default values to the worker. Pre-release with
    backward-compat off (see ``L23``); legacy keys like
    ``image_root`` / ``image_list`` / ``database_path`` were
    retired.
    """

    model_config = ConfigDict(extra="forbid")


class FeaturesRequest(_StageReqBase):
    spec: FeaturesSpec = FeaturesSpec()


class MatchesRequest(_StageReqBase):
    """Match-stage request body — pair selection + per-pair matcher
    are independent shapes (AIP-202: one concept per type)."""

    pairs: PairsSpec = PairsSpec()
    matcher: MatcherSpec = MatcherSpec()


class VerifyRequest(_StageReqBase):
    spec: VerifySpec = VerifySpec()


def _job_response(job_id: str, tasks: list) -> JSONResponse:
    return accepted_response(
        JobAcceptedResponse(job_id=job_id, task_ids=[t.task_id for t in tasks])
    )


@router.post(
    "/features",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobAcceptedResponse,
)
async def features(
    dataset_id: str,
    body: FeaturesRequest = FeaturesRequest(),
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Enqueue local-feature extraction on every image in the dataset.

    Returns 202 with a ``Location`` header pointing at the job. The
    extractor type (``sift``, ``superpoint``, ``aliked``, ...) is
    chosen via :class:`FeaturesSpec.type`; backends advertise
    supported types via the ``features.extract.{type}`` capability
    flags. Re-running with an identical spec hits the cache.
    """
    job_id, tasks = await sfm_stage_service.submit_features(
        session,
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        spec=body.spec.model_dump(mode="json"),
    )
    return _job_response(job_id, tasks)


@router.post(
    "/matches",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobAcceptedResponse,
)
async def matches(
    dataset_id: str,
    body: MatchesRequest = MatchesRequest(),
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Enqueue feature-matching across image pairs in the dataset.

    Pair selection (``body.pairs``) and per-pair matching
    (``body.matcher``) are independent shapes (AIP-202): pick pairs
    via exhaustive / sequential / spatial / vocabtree / retrieval /
    from_poses / explicit, then run any of nn-mutual / nn-ratio /
    superglue / lightglue / loftr against them. Optional provider
    fields disambiguate mixed deployments such as hloc retrieval with
    COLMAP SIFT. Requires features to have been extracted; returns
    202 + ``Location``.
    """
    job_id, tasks = await sfm_stage_service.submit_matches(
        session,
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        spec={
            "pairs": body.pairs.model_dump(mode="json"),
            "matcher": body.matcher.model_dump(mode="json"),
        },
    )
    return _job_response(job_id, tasks)


@router.post(
    "/verify",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobAcceptedResponse,
)
async def verify(
    dataset_id: str,
    body: VerifyRequest = VerifyRequest(),
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Enqueue two-view geometric verification on the matched pairs.

    Filters raw matches with RANSAC / fundamental matrix / homography
    estimation and writes the verified inlier subset to
    ``two_view_geometries.json``. Required before any mapping recipe.
    Returns 202 + ``Location`` pointing at the job.
    """
    job_id, tasks = await sfm_stage_service.submit_verify(
        session,
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        spec=body.spec.model_dump(mode="json"),
    )
    return _job_response(job_id, tasks)
