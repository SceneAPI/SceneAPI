"""/v1/projects/{pid}/pipelines/{recipe} -- sugar over the stage DAG."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from typing import Any

from app.api.v1._helpers import accepted_response
from app.core import operations as core_operations
from app.core import pipelines as core_pipelines
from app.core.errors import ValidationError
from app.core.ids import new_id
from app.core.tenancy import current_tenant
from app.db.session import get_db
from app.orchestrator.dag import TaskNode, hash_inputs, hash_params
from app.orchestrator.scheduler import submit_job_dag
from app.services import project_service
from app.schemas.api.artifacts import ArtifactInputMap
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
from app.services import artifact_service, dataset_service, sfm_stage_service

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

    model_config = ConfigDict(extra="forbid")

    dataset_id: str
    features: FeaturesSpec = FeaturesSpec()
    pairs: PairsSpec = PairsSpec()
    matcher: MatcherSpec = MatcherSpec()
    verify: VerifySpec = VerifySpec()
    spec: PipelineSpec
    input_artifacts: ArtifactInputMap = Field(
        default_factory=dict,
        description=(
            "Optional role-keyed artifact references shared by the recipe. "
            "Stage-local input_artifacts on features, pairs, matcher, verify, "
            "or spec are merged with this map."
        ),
    )


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
    input_artifacts = {
        **body.features.input_artifacts,
        **body.pairs.input_artifacts,
        **body.matcher.input_artifacts,
        **body.verify.input_artifacts,
        **body.spec.input_artifacts,
        **body.input_artifacts,
    }
    sfm_stage_service.validate_recipe_stage_configs(
        features_spec=features_spec,
        matches_spec=matches_spec,
        verify_spec=verify_spec,
        pipeline_spec=spec_dict,
        project_id=project_id,
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
    resolved_artifacts = await artifact_service.resolve_input_artifacts(
        session,
        tenant_id=tenant_id,
        dataset_id=d.dataset_id,
        input_artifacts=input_artifacts,
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
        input_artifacts=resolved_artifacts,
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


class PipelineStep(BaseModel):
    """One operation in a custom typed pipeline."""

    model_config = ConfigDict(extra="forbid")

    op: str
    provider: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class PipelineRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_id: str
    steps: list[PipelineStep]


@router.post(
    ":run",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobAcceptedResponse,
)
async def run_pipeline(
    project_id: str,
    body: PipelineRunRequest,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Submit a custom typed-operation pipeline.

    The operation sequence is type-checked against the operation contract
    BEFORE any job is created -- a type break or unknown operation is rejected
    with 422. This is where the typed model guards real submissions (unlike the
    fixed recipes, an arbitrary pipeline can be invalid). Each step binds an
    operation to an optional provider + params; the binding is resolved at
    execution by the bridge worker (shallow-by-contract submit), so submission
    is accepted once the pipeline type-checks.
    """
    await project_service.get_project(
        session, tenant_id=tenant_id, project_id=project_id
    )
    errors = core_pipelines.validate_pipeline([s.op for s in body.steps])
    if errors:
        detail = "; ".join(f"{e.where}: {e.message}" for e in errors)
        raise ValidationError(f"pipeline failed type-check: {detail}")
    await dataset_service.get_dataset(
        session, tenant_id=tenant_id, dataset_id=body.dataset_id
    )

    nodes: list[TaskNode] = []
    prev: str | None = None
    for step in body.steps:
        op = core_operations.operation_for(step.op)  # non-None: type-check passed
        assert op is not None
        task_id = new_id()
        nodes.append(
            TaskNode(
                task_id=task_id,
                kind="operation",
                inputs_hash=hash_inputs(
                    {"dataset_id": body.dataset_id, "depends_on": [prev] if prev else []}
                ),
                params_hash=hash_params(
                    {"op": step.op, "provider": step.provider, "params": step.params}
                ),
                depends_on=[prev] if prev else [],
                gpu_required=True,
                metadata={
                    "operation": step.op,
                    "provider": step.provider,
                    "params": step.params,
                    "consumes": list(op.consumes),
                    "produces": list(op.produces),
                },
            )
        )
        prev = task_id

    job_id, tasks = await submit_job_dag(
        session,
        tenant_id=tenant_id,
        project_id=project_id,
        recipe="pipeline",
        spec={
            "dataset_id": body.dataset_id,
            "steps": [s.model_dump(mode="json") for s in body.steps],
        },
        nodes=nodes,
    )
    return accepted_response(
        JobAcceptedResponse(
            job_id=job_id,
            task_ids=[t.task_id for t in tasks],
            project_id=project_id,
            dataset_id=body.dataset_id,
        )
    )
