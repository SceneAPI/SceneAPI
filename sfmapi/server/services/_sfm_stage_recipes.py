"""Recipe (multi-stage pipeline) helpers for the ``sfm_stage_service`` facade.

A *recipe* (`incremental | global | hierarchical | spherical`) is sugar
over the per-stage builders: ``build_recipe_dag`` strings extract →
match → verify → map into one DAG so per-stage caching short-circuits
as soon as any prefix is reused. The pipelines routes compose these
helpers with the shared-core materialization / reconstruction helpers.

Import through :mod:`sfmapi.server.services.sfm_stage_service`; this
underscore module is an internal layout detail.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sfmapi.server.db.models import Image
from sfmapi.server.orchestrator.dag import TaskNode
from sfmapi.server.services import artifact_service
from sfmapi.server.services._sfm_stage_core import _stage_node
from sfmapi.server.services._sfm_stage_dataset import (
    validate_features_config,
    validate_mapping_config,
    validate_matches_config,
    validate_verify_config,
)


def validate_recipe_stage_configs(
    *,
    features_spec: dict[str, Any],
    matches_spec: dict[str, Any],
    verify_spec: dict[str, Any],
    pipeline_spec: dict[str, Any],
    project_id: str | None = None,
) -> None:
    validate_features_config(features_spec, project_id=project_id)
    validate_matches_config(matches_spec, project_id=project_id)
    validate_verify_config(verify_spec, project_id=project_id)
    validate_mapping_config(pipeline_spec, project_id=project_id)


async def collect_pose_priors_by_name(
    session: AsyncSession, *, tenant_id: str, dataset_id: str
) -> dict[str, dict[str, Any]]:
    """Return ``{image_name: PosePrior dict}`` for every image in the
    dataset that has a non-null ``pose_prior_json``. Keyed by name so
    the worker can correlate with pycolmap's image-name primary key."""
    rows = (
        await session.execute(
            select(Image.name, Image.pose_prior_json).where(
                Image.tenant_id == tenant_id,
                Image.dataset_id == dataset_id,
                Image.pose_prior_json.is_not(None),
            )
        )
    ).all()
    return {name: prior for name, prior in rows if prior}


def build_recipe_dag(
    *,
    project_id: str,
    dataset_id: str,
    recon_id: str,
    materialization: dict[str, Any],
    database_path: str,
    features_spec: dict[str, Any],
    matches_spec: dict[str, Any],
    verify_spec: dict[str, Any],
    pipeline_spec: dict[str, Any],
    pose_priors: dict[str, dict[str, Any]] | None = None,
    input_artifacts: dict[str, dict[str, Any]] | None = None,
) -> list[TaskNode]:
    """Stitch extract → match → verify → map into one DAG. Each TaskNode
    is hashed with the same shape as a single-stage submission, so a
    recipe that re-uses an already-computed extract+match prefix
    short-circuits to the cached results.

    ``pose_priors`` are optional per-image priors (keyed by image name);
    when present they're forwarded into the map task's inputs so the
    worker can wire them into pycolmap's ``MappingInput``.
    """
    input_artifacts = input_artifacts or {}
    features_db_path = artifact_service.database_path_from_input_artifacts(
        input_artifacts,
        roles=("features",),
    )
    matches_db_path = artifact_service.database_path_from_input_artifacts(
        input_artifacts,
        roles=("matches",),
    )
    verified_db_path = artifact_service.database_path_from_input_artifacts(
        input_artifacts,
        roles=("verified_matches",),
    )

    extract_inputs = {
        "project_id": project_id,
        "dataset_id": dataset_id,
        "recon_id": recon_id,
        "materialization": materialization,
        "database_path": database_path,
    }
    if input_artifacts:
        extract_inputs["input_artifacts"] = input_artifacts
    extract = _stage_node(kind="extract", inputs=extract_inputs, spec=features_spec)

    common_stage_inputs = {
        "recon_id": recon_id,
        "dataset_id": dataset_id,
        "database_path": database_path,
    }
    match = _stage_node(
        kind="match",
        inputs={
            **common_stage_inputs,
            "database_path": features_db_path or database_path,
            **({"input_artifacts": input_artifacts} if input_artifacts else {}),
        },
        spec={
            **matches_spec,
            **(
                {"provider": matches_spec["matcher"]["provider"]}
                if (
                    isinstance(matches_spec.get("matcher"), dict)
                    and isinstance(matches_spec["matcher"].get("provider"), str)
                )
                else {}
            ),
        },
        depends_on=[extract.task_id],
    )
    verify = _stage_node(
        kind="verify",
        inputs={
            **common_stage_inputs,
            "database_path": matches_db_path or database_path,
            **({"input_artifacts": input_artifacts} if input_artifacts else {}),
        },
        spec=verify_spec,
        depends_on=[match.task_id],
    )
    map_inputs = {
        "project_id": project_id,
        "recon_id": recon_id,
        "dataset_id": dataset_id,
        "database_path": verified_db_path or database_path,
        "materialization": materialization,
    }
    if pose_priors:
        map_inputs["pose_priors"] = pose_priors
    if input_artifacts:
        map_inputs["input_artifacts"] = input_artifacts
    map_node = _stage_node(
        kind="map", inputs=map_inputs, spec=pipeline_spec, depends_on=[verify.task_id]
    )
    return [extract, match, verify, map_node]
