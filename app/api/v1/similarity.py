"""§6.9.3 Image similarity — k-nearest images by perceptual hash or VLAD."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1._helpers import accepted_response
from app.core.tenancy import current_tenant
from app.db.session import get_db
from app.schemas.api.jobs import JobAcceptedResponse
from app.schemas.api.similarity import (
    SimilarityBuildResponse,
    SimilarityNeighborOut,
    SimilarityQueryResponse,
)
from app.schemas.pipeline_spec import (
    PROVIDER_SELECTOR_MAX_LENGTH,
    PROVIDER_SELECTOR_PATTERN,
)
from app.services import sfm_stage_service, similarity_service

router = APIRouter(prefix="/datasets/{dataset_id}/similarity", tags=["similarity"])


@router.get("", response_model=SimilarityQueryResponse)
async def neighbors(
    dataset_id: str,
    image_id: str = Query(..., description="The image to query against."),
    k: int = Query(default=5, ge=1, le=1000),
    strategy: str = Query(default="dhash"),
    include_self: bool = Query(default=False),
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> SimilarityQueryResponse:
    """Return the k images most similar to `image_id`.

    For `strategy=dhash` the index is built lazily on first call and
    cached on disk; subsequent calls reuse the cache until the
    dataset's `manifest_hash` changes.
    """
    results = await similarity_service.query_neighbors(
        session,
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        image_id=image_id,
        k=k,
        strategy=strategy,  # type: ignore[arg-type]
        include_self=include_self,
    )
    return SimilarityQueryResponse(
        query_image_id=image_id,
        strategy=strategy,
        k=k,
        neighbors=[
            SimilarityNeighborOut(image_id=n.image_id, distance=n.distance) for n in results
        ],
    )


@router.post(
    ":build",
    status_code=status.HTTP_200_OK,
    responses={
        200: {"model": SimilarityBuildResponse},
        202: {"model": JobAcceptedResponse},
    },
)
async def build(
    dataset_id: str,
    strategy: str = Query(default="dhash"),
    force: bool = Query(default=True),
    provider: str | None = Query(
        default=None,
        min_length=1,
        max_length=PROVIDER_SELECTOR_MAX_LENGTH,
        pattern=PROVIDER_SELECTOR_PATTERN,
        description="Optional provider id to execute a vlad build job.",
    ),
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Build (or rebuild) the similarity index for the dataset.

    `dhash` builds synchronously using the optional image-processing
    dependency. `vlad` enqueues a worker job (requires pycolmap + SIFT
    extraction per image) and returns ``202`` with a `Location` header
    pointing at the job.
    """
    if strategy == "vlad":
        job_id, _tasks, resolved_provider = await sfm_stage_service.submit_vlad_index(
            session, tenant_id=tenant_id, dataset_id=dataset_id, provider=provider
        )
        return accepted_response(
            JobAcceptedResponse(
                job_id=job_id,
                dataset_id=dataset_id,
                strategy="vlad",
                provider=resolved_provider,
            )
        )
    index = await similarity_service.build_index(
        session,
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        strategy=strategy,  # type: ignore[arg-type]
        force=force,
    )
    payload = SimilarityBuildResponse(
        strategy=index.strategy,
        manifest_hash=index.manifest_hash,
        count=len(index.hashes),
    )
    return JSONResponse(payload.model_dump(), status_code=status.HTTP_200_OK)
