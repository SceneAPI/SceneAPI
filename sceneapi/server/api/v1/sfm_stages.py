"""SfM stage endpoints: features, matches, verify.

Each call returns 202 + a `Location` header pointing at the job. The
request body is now just the spec — image source and database paths are
derived server-side from the dataset's `source` and the cached
reconstruction.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from sceneapi.server.api.v1._helpers import accepted_response
from sceneapi.server.core.tenancy import current_tenant
from sceneapi.server.db.session import get_db
from sceneapi.server.schemas.api.artifacts import ArtifactInputMap
from sceneapi.server.schemas.api.jobs import JobAcceptedResponse
from sceneapi.server.schemas.pipeline_spec import FeaturesSpec, MatcherSpec, PairsSpec, VerifySpec
from sceneapi.server.services import sfm_stage_service

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
    input_artifacts: ArtifactInputMap = Field(default_factory=dict)


class VerifyRequest(_StageReqBase):
    spec: VerifySpec = VerifySpec()
    input_artifacts: ArtifactInputMap = Field(default_factory=dict)


def _job_response(job_id: str, tasks: list[Any], provider: str | None = None) -> JSONResponse:
    """Build the canonical 202 envelope.

    ``provider`` is the routing-resolved provider id read back off the
    mutated stage spec (``apply_provider_resolution`` writes it in place).
    The server may resolve a provider the client never named — echoing it
    here is the only way the client can learn which backend was chosen.
    """
    return accepted_response(
        JobAcceptedResponse(job_id=job_id, task_ids=[t.task_id for t in tasks], provider=provider)
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
    # Hold the spec dict: submit_features -> validate_features_config ->
    # apply_provider_resolution mutates it in place with the resolved
    # provider, which we then echo back on the 202.
    spec = body.spec.model_dump(mode="json")
    job_id, tasks = await sfm_stage_service.submit_features(
        session,
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        spec=spec,
    )
    return _job_response(job_id, tasks, spec.get("provider"))


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
    # validate_matches_config resolves pairs.provider and matcher.provider
    # separately, mutating these sub-dicts in place. The matcher provider
    # is the headline op, so echo that one on the 202.
    spec = {
        "pairs": body.pairs.model_dump(mode="json"),
        "matcher": body.matcher.model_dump(mode="json"),
    }
    job_id, tasks = await sfm_stage_service.submit_matches(
        session,
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        spec=spec,
        input_artifacts={
            **body.pairs.input_artifacts,
            **body.matcher.input_artifacts,
            **body.input_artifacts,
        },
    )
    return _job_response(job_id, tasks, spec["matcher"].get("provider"))


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
    spec = body.spec.model_dump(mode="json")
    job_id, tasks = await sfm_stage_service.submit_verify(
        session,
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        spec=spec,
        input_artifacts={**body.spec.input_artifacts, **body.input_artifacts},
    )
    return _job_response(job_id, tasks, spec.get("provider"))
