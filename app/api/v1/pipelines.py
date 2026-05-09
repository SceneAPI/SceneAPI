"""/v1/projects/{pid}/pipelines/{recipe} -- sugar over the stage DAG."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1._helpers import accepted_response
from app.core.errors import ValidationError
from app.core.tenancy import current_tenant
from app.db.session import get_db
from app.orchestrator.scheduler import submit_job_dag
from app.schemas.api.jobs import JobAcceptedResponse
from app.schemas.pipeline_spec import (
    FeaturesSpec,
    GlobalSpec,
    HierarchicalSpec,
    IncrementalSpec,
    MatcherSpec,
    PairsSpec,
    PipelineSpec,
    SphericalSpec,
    VerifySpec,
)
from app.services import dataset_service, sfm_stage_service

router = APIRouter(
    prefix="/projects/{project_id}/pipelines",
    tags=["pipelines"],
)


class PipelineRequest(BaseModel):
    """End-to-end pipeline request — features + pair selection +
    matcher + two-view verification + mapping spec, sent in one body
    for the recipe routes (``/pipelines/{incremental|global|...}``).

    Pair selection (``pairs``) and per-pair matching (``matcher``) are
    independent shapes (AIP-202)."""

    dataset_id: str
    features: FeaturesSpec = FeaturesSpec()
    pairs: PairsSpec = PairsSpec()
    matcher: MatcherSpec = MatcherSpec()
    verify: VerifySpec = VerifySpec()
    spec: PipelineSpec


_RECIPE_TO_KIND = {
    "incremental": IncrementalSpec,
    "global": GlobalSpec,
    "hierarchical": HierarchicalSpec,
    "spherical": SphericalSpec,
}


@router.post(
    "/{recipe}",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobAcceptedResponse,
)
async def run_recipe(
    project_id: str,
    recipe: Literal["incremental", "global", "hierarchical", "spherical"],
    body: PipelineRequest,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Run an end-to-end mapping recipe in one POST.

    Composes ``features -> matches -> verify -> map -> ba -> ...``
    into a single job DAG keyed on ``recipe`` (one of ``incremental``
    | ``global`` | ``hierarchical`` | ``spherical``). The recipe MUST
    match ``body.spec.kind`` — 422 ``ValidationError`` if not. Each
    stage spec keeps optional provider selectors
    so mixed deployments can route hloc and COLMAP implementations
    behind the same portable capability names. Each backend advertises
    which recipes it implements via the
    ``pipelines.{kind}`` capability flags; unsupported recipes
    return ``501 capability_unavailable``. Returns 202 + a
    ``Location`` header pointing at the parent job.
    """
    expected = _RECIPE_TO_KIND[recipe]
    if body.spec.kind != recipe:
        raise ValidationError(f"spec.kind={body.spec.kind} does not match recipe={recipe}")
    d = await dataset_service.get_dataset(session, tenant_id=tenant_id, dataset_id=body.dataset_id)
    spec_dict = body.spec.model_dump(mode="json")
    features_spec = body.features.model_dump(mode="json")
    verify_spec = body.verify.model_dump(mode="json")
    matches_spec = {
        "pairs": body.pairs.model_dump(mode="json"),
        "matcher": body.matcher.model_dump(mode="json"),
    }
    sfm_stage_service.validate_recipe_stage_configs(
        features_spec=features_spec,
        matches_spec=matches_spec,
        verify_spec=verify_spec,
        pipeline_spec=spec_dict,
    )
    r = await sfm_stage_service.ensure_reconstruction(
        session, tenant_id=tenant_id, dataset=d, spec=spec_dict
    )
    materialization = await sfm_stage_service.derive_materialization(
        session, tenant_id=tenant_id, dataset=d
    )
    db_path = sfm_stage_service.reconstruction_database_path(tenant_id, project_id, r.recon_id)
    pose_priors = await sfm_stage_service.collect_pose_priors_by_name(
        session, tenant_id=tenant_id, dataset_id=d.dataset_id
    )
    nodes = sfm_stage_service.build_recipe_dag(
        project_id=project_id,
        dataset_id=d.dataset_id,
        recon_id=r.recon_id,
        materialization=materialization,
        database_path=db_path,
        features_spec=features_spec,
        matches_spec=matches_spec,
        verify_spec=verify_spec,
        pipeline_spec=spec_dict,
        pose_priors=pose_priors or None,
    )
    job_id, tasks = await submit_job_dag(
        session,
        tenant_id=tenant_id,
        project_id=project_id,
        recipe=recipe,
        spec={
            "features": features_spec,
            "matches": matches_spec,
            "verify": verify_spec,
            "spec": spec_dict,
        },
        nodes=nodes,
    )
    _ = expected  # type: ignore[unused-ignore]  # only checked for kind/recipe coherence
    return accepted_response(
        JobAcceptedResponse(
            job_id=job_id,
            task_ids=[t.task_id for t in tasks],
            recon_id=r.recon_id,
        )
    )
