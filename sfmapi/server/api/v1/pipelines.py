"""/v1/projects/{pid}/pipelines/{recipe} -- sugar over the stage DAG."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from sfmapi.server.api.v1._helpers import accepted_response
from sfmapi.server.api.v1.dataflow import (
    _canonicalize_initial_input_wires,
    _effective_registry_or_error,
    _initial_input_errors,
    legacy_operation_projection_errors,
)
from sfmapi.server.core import pipelines as core_pipelines
from sfmapi.server.core.errors import CapabilityUnavailableError, ValidationError
from sfmapi.server.core.tenancy import current_tenant
from sfmapi.server.db.session import get_db
from sfmapi.server.orchestrator.scheduler import submit_job_dag
from sfmapi.server.schemas.api.artifacts import ArtifactInputMap
from sfmapi.server.schemas.api.common import ProblemResponse
from sfmapi.server.schemas.api.dataflow import (
    PipelineStepIn,
    core_steps,
    is_executable_legacy_sfm_pipeline,
    legacy_operation_ids,
    provider_errors,
    should_use_legacy_validation,
    step_params,
    step_provider,
)
from sfmapi.server.schemas.api.jobs import JobAcceptedResponse
from sfmapi.server.schemas.pipeline_spec import (
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
from sfmapi.server.services import (
    artifact_service,
    dataset_service,
    project_service,
    sfm_stage_service,
)

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

    Composes ``features -> matches -> verify -> map`` into a single
    job DAG keyed on ``recipe`` (one of ``incremental``
    | ``global`` | ``hierarchical`` | ``spherical``). The recipe MUST
    match ``body.spec.kind`` — 422 ``ValidationError`` if not. Each
    stage spec keeps optional provider selectors
    so mixed deployments can route hloc and COLMAP implementations
    behind the same portable capability names. Each backend advertises
    which mapping stages it implements via the ``map.{kind}``
    capability flags. Unsupported stage capabilities fail through the
    submitted job's task status. Returns 202 + a ``Location`` header
    pointing at the parent job.
    """
    expected = _RECIPE_TO_KIND[recipe]
    if body.spec.kind != recipe:
        raise ValidationError(f"spec.kind={body.spec.kind} does not match recipe={recipe}")
    d = await dataset_service.get_dataset(
        session,
        tenant_id=tenant_id,
        dataset_id=body.dataset_id,
        project_id=project_id,
    )
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
    resolved_artifacts = await artifact_service.resolve_input_artifacts(
        session,
        tenant_id=tenant_id,
        dataset_id=d.dataset_id,
        input_artifacts=input_artifacts,
    )
    materialization = await sfm_stage_service.derive_materialization(
        session, tenant_id=tenant_id, dataset=d
    )
    r = await sfm_stage_service.ensure_reconstruction(
        session, tenant_id=tenant_id, dataset=d, spec=spec_dict
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
        input_artifacts=resolved_artifacts,
    )
    job_spec: dict[str, Any] = {
        "features": features_spec,
        "matches": matches_spec,
        "verify": verify_spec,
        "spec": spec_dict,
    }
    if resolved_artifacts:
        job_spec["input_artifacts"] = resolved_artifacts
    job_id, tasks = await submit_job_dag(
        session,
        tenant_id=tenant_id,
        project_id=project_id,
        recipe=recipe,
        spec=job_spec,
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


class PipelineRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_id: str
    initial_inputs: list[str] = Field(
        default_factory=lambda: list(core_pipelines.DEFAULT_INITIAL_INPUTS),
        description=(
            "Legacy compatibility list of initial DataType ids available as "
            "synthetic inputs.* ports. New Processor pipelines should prefer "
            "reference-keyed initial inputs when that durable shape is enabled."
        ),
    )
    steps: list[str | PipelineStepIn] = Field(min_length=1)


def _legacy_sfm_specs(
    steps: list[str | PipelineStepIn],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    params_by_op: dict[str, tuple[int, dict[str, Any]]] = {}
    for index, step in enumerate(steps):
        op_id = step if isinstance(step, str) else step.op
        params = step_params(step)
        provider = step_provider(step)
        if provider:
            params["provider"] = provider
        params_by_op[op_id] = (index, params)

    def validate_stage(
        op_id: str,
        model: type[BaseModel],
    ) -> dict[str, Any]:
        index, params = params_by_op.get(op_id, (-1, {}))
        try:
            return model.model_validate(params).model_dump(mode="json")
        except PydanticValidationError as exc:
            raise _legacy_spec_validation_error(op_id, index, exc) from exc

    features_spec = validate_stage("features", FeaturesSpec)
    pairs_spec = validate_stage("pairs", PairsSpec)
    matcher_spec = validate_stage("matches", MatcherSpec)
    verify_spec = validate_stage("verify", VerifySpec)

    map_index, map_params = params_by_op.get("map", (-1, {}))
    raw_map_spec = dict(map_params)
    kind = str(raw_map_spec.get("kind") or "incremental")
    spec_model = _RECIPE_TO_KIND.get(kind)
    if spec_model is None:
        raise ValidationError(
            f"unsupported legacy mapping kind: {kind}",
            errors=[
                {
                    "loc": ["body", "steps", map_index, "params", "kind"],
                    "msg": f"unsupported legacy mapping kind: {kind}",
                    "type": "invalid_attribute",
                    "ctx": {
                        "where": f"step {map_index} 'map'",
                        "reason": "invalid_attribute",
                        "path": f"steps.{map_index}.params.kind",
                    },
                }
            ],
        )
    try:
        pipeline_spec = spec_model.model_validate(raw_map_spec).model_dump(mode="json")
    except PydanticValidationError as exc:
        raise _legacy_spec_validation_error("map", map_index, exc) from exc
    return features_spec, pairs_spec, matcher_spec, verify_spec, pipeline_spec


def _legacy_spec_validation_error(
    op_id: str,
    index: int,
    exc: PydanticValidationError,
) -> ValidationError:
    errors: list[dict[str, Any]] = []
    first_msg = "invalid legacy stage parameters"
    for err in exc.errors():
        loc = list(err.get("loc") or [])
        loc_parts = [str(part) for part in loc]
        if loc_parts and first_msg == "invalid legacy stage parameters":
            first_msg = str(err.get("msg") or first_msg)
        path = ".".join(["steps", str(index), "params", *loc_parts])
        errors.append(
            {
                "loc": ["body", "steps", index, "params", *loc],
                "msg": err.get("msg", "invalid legacy stage parameters"),
                "type": "invalid_attribute",
                "ctx": {
                    "where": f"step {index} '{op_id}'",
                    "reason": "invalid_attribute",
                    "path": path,
                },
            }
        )
    if not errors:
        errors.append(
            {
                "loc": ["body", "steps", index, "params"],
                "msg": first_msg,
                "type": "invalid_attribute",
                "ctx": {
                    "where": f"step {index} '{op_id}'",
                    "reason": "invalid_attribute",
                    "path": f"steps.{index}.params",
                },
            }
        )
    return ValidationError(
        f"pipeline failed type-check: step {index} '{op_id}': {first_msg}",
        errors=errors,
    )


def _pipeline_validation_payload(
    errors: list[core_pipelines.ChainError],
) -> list[dict[str, Any]]:
    def loc_from_path(path: str | None) -> list[str | int] | None:
        if not path:
            return None
        loc: list[str | int] = ["body"]
        parts = path.split(".")
        i = 0
        while i < len(parts):
            part = parts[i]
            loc.append(int(part) if part.isdigit() else part)
            if part in {"attributes", "wires"} and i + 1 < len(parts):
                loc.append(".".join(parts[i + 1 :]))
                break
            i += 1
        return loc

    return [
        {
            "loc": loc_from_path(e.path),
            "msg": e.message,
            "type": e.reason or "value_error",
            "ctx": {
                "where": e.where,
                "reason": e.reason,
                "path": e.path,
            },
        }
        for e in errors
    ]


@router.post(
    ":run",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobAcceptedResponse,
    response_description=(
        "Legacy operation pipeline accepted; native typed processor execution "
        "returns 501 until the generic executor lands."
    ),
    responses={
        404: {"model": ProblemResponse, "description": "Project or dataset not found."},
        422: {"model": ProblemResponse, "description": "Pipeline type-check failed."},
        501: {
            "model": ProblemResponse,
            "description": "Custom typed processor execution is not available.",
        },
    },
)
async def run_pipeline(
    project_id: str,
    body: PipelineRunRequest,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Submit a pipeline through the typed Processor preflight surface.

    Processor steps are type-checked against named consumer/supplier ports
    before any job is created. Legacy operation-id list steps remain accepted
    as a compatibility input shape and are projected through the legacy
    operation contract. The legacy flat SfM chain is routed through the recipe
    DAG executor and returns 202. Native typed DAG execution is not available
    yet, so type-valid native requests return 501 after project/dataset
    validation rather than creating jobs that would fail later as ``UnknownTask``.
    """
    await project_service.get_project(session, tenant_id=tenant_id, project_id=project_id)
    normalized_steps = core_steps(body.steps)
    registry = _effective_registry_or_error()
    initial_inputs = tuple(registry.canonical_datatype(type_id) for type_id in body.initial_inputs)
    input_errors = _initial_input_errors(
        initial_inputs,
        datatype_lookup=registry.has_datatype,
    )
    errors: list[core_pipelines.ChainError] = []
    use_legacy_validation = should_use_legacy_validation(body.steps)
    legacy_operation_tuple = (
        tuple(legacy_operation_ids(body.steps)) if use_legacy_validation else ()
    )
    executable_legacy_sfm = is_executable_legacy_sfm_pipeline(body.steps)

    if use_legacy_validation:
        errors.extend(input_errors)
        errors.extend(legacy_operation_projection_errors(body.steps))
        errors.extend(
            core_pipelines.validate_pipeline(
                legacy_operation_tuple,
                initial_inputs=initial_inputs,
                processor_lookup=registry.processor_for,
            )
        )
        if not executable_legacy_sfm:
            errors.extend(
                core_pipelines.validate_step_attributes(
                    normalized_steps,
                    processor_lookup=registry.processor_for,
                )
            )
    else:
        errors.extend(input_errors)
        normalized_steps = _canonicalize_initial_input_wires(
            normalized_steps,
            registry=registry,
        )
        validation_errors = core_pipelines.validate_pipeline(
            normalized_steps,
            initial_inputs=initial_inputs,
            processor_lookup=registry.processor_for,
        )
        errors.extend(
            error for error in validation_errors if error.reason != "duplicate_initial_input"
        )
    if use_legacy_validation and not executable_legacy_sfm:
        errors.extend(provider_errors(body.steps))
    if errors:
        detail = "; ".join(f"{e.where}: {e.message}" for e in errors)
        raise ValidationError(
            f"pipeline failed type-check: {detail}",
            errors=_pipeline_validation_payload(errors),
        )
    d = await dataset_service.get_dataset(
        session,
        tenant_id=tenant_id,
        dataset_id=body.dataset_id,
        project_id=project_id,
    )
    if use_legacy_validation and executable_legacy_sfm:
        (
            features_spec,
            pairs_spec,
            matcher_spec,
            verify_spec,
            pipeline_spec,
        ) = _legacy_sfm_specs(body.steps)
        matches_spec = {"pairs": pairs_spec, "matcher": matcher_spec}
        sfm_stage_service.validate_recipe_stage_configs(
            features_spec=features_spec,
            matches_spec=matches_spec,
            verify_spec=verify_spec,
            pipeline_spec=pipeline_spec,
            project_id=project_id,
        )
        input_artifacts = {
            **features_spec.get("input_artifacts", {}),
            **pairs_spec.get("input_artifacts", {}),
            **matcher_spec.get("input_artifacts", {}),
            **verify_spec.get("input_artifacts", {}),
            **pipeline_spec.get("input_artifacts", {}),
        }
        resolved_artifacts = await artifact_service.resolve_input_artifacts(
            session,
            tenant_id=tenant_id,
            dataset_id=d.dataset_id,
            input_artifacts=input_artifacts,
        )
        materialization = await sfm_stage_service.derive_materialization(
            session, tenant_id=tenant_id, dataset=d
        )
        r = await sfm_stage_service.ensure_reconstruction(
            session, tenant_id=tenant_id, dataset=d, spec=pipeline_spec
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
            pipeline_spec=pipeline_spec,
            pose_priors=pose_priors or None,
            input_artifacts=resolved_artifacts,
        )
        job_spec: dict[str, Any] = {
            "features": features_spec,
            "matches": matches_spec,
            "verify": verify_spec,
            "spec": pipeline_spec,
        }
        if resolved_artifacts:
            job_spec["input_artifacts"] = resolved_artifacts
        job_id, tasks = await submit_job_dag(
            session,
            tenant_id=tenant_id,
            project_id=project_id,
            recipe=str(pipeline_spec.get("kind") or "incremental"),
            spec=job_spec,
            nodes=nodes,
        )
        return accepted_response(
            JobAcceptedResponse(
                job_id=job_id,
                project_id=project_id,
                dataset_id=d.dataset_id,
                task_ids=[t.task_id for t in tasks],
                recon_id=r.recon_id,
            )
        )
    raise CapabilityUnavailableError(
        capability="pipelines.custom_execution",
        reason=(
            "custom typed processor pipeline execution is not available in "
            "this deployment; use /v1/pipelines:validate for preflight "
            "type-checking"
        ),
    )
