"""Dataset-scoped stage submits for the ``sfm_stage_service`` facade.

The SfM dataset stages (features / matches / verify), their config
validators, the utility / projection stages that operate on a dataset
(``project_images`` / ``render_cubemap`` / ``video_frames`` /
``kapture_import`` / ``import_archive`` / ``vlad_index``), and the
table-driven feature-database stages (``vocab_tree`` /
``configure_rig`` / ``two_view``).

Import through :mod:`sceneapi.server.services.sfm_stage_service`; this
underscore module is an internal layout detail.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sceneapi.server.adapters import backend_config
from sceneapi.server.core.capabilities import require as require_capability
from sceneapi.server.core.config import get_settings
from sceneapi.server.core.errors import ValidationError
from sceneapi.server.core.ids import new_id
from sceneapi.server.core.paths import Paths
from sceneapi.server.core.projections import projection_capability
from sceneapi.server.db.models import Blob, Dataset, Image
from sceneapi.server.schemas.api.projections import CubemapProjectionSpec, ProjectionJobRequest
from sceneapi.server.services import (
    artifact_service,
    dataset_service,
    project_service,
    provider_routing_service,
)
from sceneapi.server.services._sfm_stage_core import (
    _merge_spec_input_artifacts,
    _resolve_database_path,
    _routing_workspace,
    _stage_backend_options,
    _StageDef,
    _submit_single_stage,
    derive_materialization,
    ensure_reconstruction,
    reconstruction_database_path,
)


def validate_features_config(spec: dict[str, Any], *, project_id: str | None = None) -> None:
    feature_type = str(spec.get("type") or "sift")
    provider_routing_service.apply_provider_resolution(
        spec,
        stage="features",
        capability=f"features.extract.{feature_type}",
        project_id=project_id,
        workspace=_routing_workspace(),
    )
    backend_config.validate_backend_options(
        stage="features",
        capability=f"features.extract.{feature_type}",
        provider=spec.get("provider"),
        options=_stage_backend_options(spec, stage="features"),
    )


def validate_matches_config(spec: dict[str, Any], *, project_id: str | None = None) -> None:
    pairs = spec.get("pairs", {})
    matcher = spec.get("matcher", {})
    if not isinstance(pairs, dict):
        raise ValidationError("spec.pairs must be a dict")
    if not isinstance(matcher, dict):
        raise ValidationError("spec.matcher must be a dict")
    strategy = str(pairs.get("strategy") or "exhaustive")
    matcher_type = str(matcher.get("type") or "nn-mutual")
    provider_routing_service.apply_provider_resolution(
        pairs,
        stage="pairs",
        capability=f"pairs.{strategy}",
        project_id=project_id,
        workspace=_routing_workspace(),
    )
    provider_routing_service.apply_provider_resolution(
        matcher,
        stage="matcher",
        capability=f"matchers.{matcher_type}",
        project_id=project_id,
        workspace=_routing_workspace(),
    )
    backend_config.validate_backend_options(
        stage="pairs",
        capability=f"pairs.{strategy}",
        provider=pairs.get("provider"),
        options=_stage_backend_options(pairs, stage="pairs"),
    )
    backend_config.validate_backend_options(
        stage="matcher",
        capability=f"matchers.{matcher_type}",
        provider=matcher.get("provider"),
        options=_stage_backend_options(matcher, stage="matcher"),
    )


def validate_verify_config(spec: dict[str, Any], *, project_id: str | None = None) -> None:
    provider_routing_service.apply_provider_resolution(
        spec,
        stage="verify",
        capability="matches.verify",
        project_id=project_id,
        workspace=_routing_workspace(),
    )
    backend_config.validate_backend_options(
        stage="verify",
        capability="matches.verify",
        provider=spec.get("provider"),
        options=_stage_backend_options(spec, stage="verify"),
    )


def validate_mapping_config(spec: dict[str, Any], *, project_id: str | None = None) -> None:
    kind = str(spec.get("kind") or "incremental")
    provider_routing_service.apply_provider_resolution(
        spec,
        stage="mapping",
        capability=f"map.{kind}",
        project_id=project_id,
        workspace=_routing_workspace(),
    )
    backend_config.validate_backend_options(
        stage="mapping",
        capability=f"map.{kind}",
        provider=spec.get("provider"),
        options=_stage_backend_options(spec, stage="mapping"),
    )


async def submit_features(
    session: AsyncSession,
    *,
    tenant_id: str,
    dataset_id: str,
    spec: dict[str, Any],
    input_artifacts: dict[str, Any] | None = None,
    inline: bool = False,
) -> tuple[str, list[Any]]:
    d = await dataset_service.get_dataset(session, tenant_id=tenant_id, dataset_id=dataset_id)
    validate_features_config(spec, project_id=d.project_id)
    r = await ensure_reconstruction(session, tenant_id=tenant_id, dataset=d, spec=spec)
    materialization = await derive_materialization(session, tenant_id=tenant_id, dataset=d)
    db_path = reconstruction_database_path(tenant_id, d.project_id, r.recon_id)
    inputs = {
        "project_id": d.project_id,
        "dataset_id": d.dataset_id,
        "recon_id": r.recon_id,
        "manifest_hash": d.manifest_hash,
        "materialization": materialization,
        "database_path": db_path,
    }
    resolved_artifacts = await artifact_service.resolve_input_artifacts(
        session,
        tenant_id=tenant_id,
        dataset_id=d.dataset_id,
        input_artifacts=input_artifacts or spec.get("input_artifacts"),
    )
    if resolved_artifacts:
        inputs["input_artifacts"] = resolved_artifacts
    return await _submit_single_stage(
        session,
        tenant_id=tenant_id,
        project_id=d.project_id,
        recipe="features",
        kind="extract",
        inputs=inputs,
        spec=spec,
        inline=inline,
    )


async def submit_matches(
    session: AsyncSession,
    *,
    tenant_id: str,
    dataset_id: str,
    spec: dict[str, Any],
    input_artifacts: dict[str, Any] | None = None,
    inline: bool = False,
) -> tuple[str, list[Any]]:
    d = await dataset_service.get_dataset(session, tenant_id=tenant_id, dataset_id=dataset_id)
    validate_matches_config(spec, project_id=d.project_id)
    pairs = spec.get("pairs", {})
    if not isinstance(pairs, dict):
        raise ValidationError("spec.pairs must be a dict")
    if pairs.get("strategy") == "vocabtree" and not pairs.get("vocab_tree_path"):
        raise ValidationError("pairs.vocab_tree_path is required for pairs.strategy=vocabtree")
    raw_input_artifacts = input_artifacts or _merge_spec_input_artifacts(
        spec.get("input_artifacts"),
        pairs.get("input_artifacts"),
        (spec.get("matcher") or {}).get("input_artifacts")
        if isinstance(spec.get("matcher"), dict)
        else None,
    )
    await _validate_explicit_pairs(
        session,
        tenant_id=tenant_id,
        dataset=d,
        pairs=pairs,
        input_artifacts=raw_input_artifacts,
    )
    r, db_path = await _resolve_database_path(session, tenant_id=tenant_id, dataset=d, spec=spec)
    resolved_artifacts = await artifact_service.resolve_input_artifacts(
        session,
        tenant_id=tenant_id,
        dataset_id=d.dataset_id,
        input_artifacts=raw_input_artifacts,
    )
    selected_db_path = artifact_service.database_path_from_input_artifacts(
        resolved_artifacts,
        roles=("features",),
    )
    if selected_db_path:
        db_path = selected_db_path
    inputs: dict[str, Any] = {
        "dataset_id": d.dataset_id,
        "recon_id": r.recon_id,
        "manifest_hash": d.manifest_hash,
        "database_path": db_path,
    }
    if resolved_artifacts:
        inputs["input_artifacts"] = resolved_artifacts
    return await _submit_single_stage(
        session,
        tenant_id=tenant_id,
        project_id=d.project_id,
        recipe="matches",
        kind="match",
        inputs=inputs,
        spec=spec,
        inline=inline,
    )


async def _validate_explicit_pairs(
    session: AsyncSession,
    *,
    tenant_id: str,
    dataset: Dataset,
    pairs: dict[str, Any],
    input_artifacts: dict[str, Any] | None = None,
) -> None:
    has_pair_artifact = bool((input_artifacts or {}).get("pairs"))
    if pairs.get("strategy") != "explicit":
        if pairs.get("image_pairs") or pairs.get("pairs_blob_sha") or has_pair_artifact:
            raise ValidationError(
                "pairs.image_pairs, pairs.pairs_blob_sha, and input_artifacts.pairs "
                "require pairs.strategy=explicit"
            )
        return

    image_pairs = pairs.get("image_pairs") or []
    pairs_blob_sha = pairs.get("pairs_blob_sha")
    has_inline = bool(image_pairs)
    has_blob = bool(pairs_blob_sha)
    if sum(bool(value) for value in (has_inline, has_blob, has_pair_artifact)) != 1:
        raise ValidationError(
            "pairs.strategy=explicit requires exactly one of pairs.image_pairs "
            "or pairs.pairs_blob_sha, or input_artifacts.pairs"
        )

    if has_pair_artifact:
        return

    if has_blob:
        blob = await session.get(Blob, str(pairs_blob_sha))
        if blob is None:
            raise ValidationError(
                f"pairs.pairs_blob_sha={pairs_blob_sha!r} does not reference a finalized blob"
            )
        return

    result = await session.execute(
        select(Image.name).where(
            Image.tenant_id == tenant_id,
            Image.dataset_id == dataset.dataset_id,
        )
    )
    known_names = {str(name) for (name,) in result.all()}
    missing: list[str] = []
    for index, pair in enumerate(image_pairs):
        if not isinstance(pair, dict):
            raise ValidationError(f"pairs.image_pairs[{index}] must be an object")
        image_name1 = str(pair.get("image_name1") or "")
        image_name2 = str(pair.get("image_name2") or "")
        if not image_name1 or not image_name2:
            raise ValidationError(
                f"pairs.image_pairs[{index}] requires image_name1 and image_name2"
            )
        if image_name1 == image_name2:
            raise ValidationError(f"pairs.image_pairs[{index}] must reference two different images")
        for image_name in (image_name1, image_name2):
            if image_name not in known_names:
                missing.append(image_name)
    if missing:
        missing_preview = ", ".join(sorted(set(missing))[:5])
        raise ValidationError(
            f"pairs.image_pairs references unknown dataset images: {missing_preview}"
        )


async def submit_verify(
    session: AsyncSession,
    *,
    tenant_id: str,
    dataset_id: str,
    spec: dict[str, Any],
    input_artifacts: dict[str, Any] | None = None,
    inline: bool = False,
) -> tuple[str, list[Any]]:
    d = await dataset_service.get_dataset(session, tenant_id=tenant_id, dataset_id=dataset_id)
    validate_verify_config(spec, project_id=d.project_id)
    r, db_path = await _resolve_database_path(session, tenant_id=tenant_id, dataset=d, spec=spec)
    resolved_artifacts = await artifact_service.resolve_input_artifacts(
        session,
        tenant_id=tenant_id,
        dataset_id=d.dataset_id,
        input_artifacts=input_artifacts or spec.get("input_artifacts"),
    )
    selected_db_path = artifact_service.database_path_from_input_artifacts(
        resolved_artifacts,
        roles=("matches",),
    )
    if selected_db_path:
        db_path = selected_db_path
    inputs: dict[str, Any] = {
        "dataset_id": d.dataset_id,
        "recon_id": r.recon_id,
        "manifest_hash": d.manifest_hash,
        "database_path": db_path,
    }
    if resolved_artifacts:
        inputs["input_artifacts"] = resolved_artifacts
    return await _submit_single_stage(
        session,
        tenant_id=tenant_id,
        project_id=d.project_id,
        recipe="verify",
        kind="verify",
        inputs=inputs,
        spec=spec,
        inline=inline,
    )


async def submit_render_cubemap(
    session: AsyncSession,
    *,
    tenant_id: str,
    dataset_id: str,
    face_size: int | None = None,
    spec: dict[str, Any] | None = None,
    provider: str | None = None,
    inline: bool = False,
) -> tuple[str, list[Any], str | None]:
    """Render every spherical panorama into 6 cubemap faces.

    Refuses if the dataset isn't ``is_spherical=true``. The output is a
    directory under the dataset's workspace; the user can register it
    as a new ``local`` dataset for downstream pinhole-only pipelines.

    ``provider`` and any ``provider`` already on the supplied ``spec``
    dict (e.g. when callers passed a pre-built operation spec) are
    folded onto the ``ProjectionJobRequest`` so the worker can route
    correctly. Returns ``(job_id, tasks, resolved_provider)``.
    """
    spec_dict = dict(spec or {})
    inner_provider = spec_dict.pop("provider", None)
    cubemap_spec = CubemapProjectionSpec.model_validate(spec_dict)
    if face_size is not None:
        cubemap_spec.face_size = int(face_size)
    request = ProjectionJobRequest(
        operation="equirectangular_to_cubemap",
        cubemap=cubemap_spec,
        provider=provider or inner_provider,
    )
    return await submit_project_images(
        session,
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        spec=request.model_dump(mode="json"),
        recipe="render_cubemap",
        inline=inline,
    )


async def submit_project_images(
    session: AsyncSession,
    *,
    tenant_id: str,
    dataset_id: str,
    spec: dict[str, Any],
    recipe: str = "project_images",
    inline: bool = False,
) -> tuple[str, list[Any], str | None]:
    """Submit a portable image projection job over a dataset.

    Returns ``(job_id, tasks, resolved_provider)`` — the third element
    is the provider routing actually selected (echoed by the route)."""
    request = ProjectionJobRequest.model_validate(spec)
    capability = projection_capability(request.operation)
    require_capability(capability)
    d = await dataset_service.get_dataset(session, tenant_id=tenant_id, dataset_id=dataset_id)
    if (
        request.operation
        in {
            "equirectangular_to_cubemap",
            "equirectangular_to_perspective",
        }
        and not d.is_spherical
    ):
        raise ValidationError(
            f"{request.operation} is only valid on datasets marked is_spherical=true"
        )
    materialization = await derive_materialization(session, tenant_id=tenant_id, dataset=d)
    paths = Paths(get_settings())
    dataset_dir = paths.dataset_root(tenant_id, d.project_id, d.dataset_id)
    inputs = {
        "dataset_id": d.dataset_id,
        "materialization": materialization,
        "dataset_dir": str(dataset_dir),
    }
    spec_dict = request.model_dump(mode="json")
    provider_routing_service.apply_provider_resolution(
        spec_dict,
        stage=recipe,
        capability=capability,
        project_id=d.project_id,
        workspace=_routing_workspace(),
    )
    job_id, tasks = await _submit_single_stage(
        session,
        tenant_id=tenant_id,
        project_id=d.project_id,
        recipe=recipe,
        kind="project_images",
        inputs=inputs,
        spec=spec_dict,
        inline=inline,
    )
    return job_id, tasks, spec_dict.get("provider")


async def submit_video_frames(
    session: AsyncSession,
    *,
    tenant_id: str,
    project_id: str,
    video_path: str,
    fps: float = 2.0,
    max_frames: int = 1000,
    inline: bool = False,
) -> tuple[str, list[Any]]:
    """Extract keyframes from a worker-local video file."""
    require_capability("video.frame_extract")
    # 404 on an unknown project instead of creating an orphan Job
    # (Job.project_id is an FK) — parity with the dataset-create route.
    await project_service.get_project(session, tenant_id=tenant_id, project_id=project_id)
    paths = Paths(get_settings())
    output_dir = paths.workspace_root / "_video_frames" / new_id()
    spec = {"fps": fps, "max_frames": max_frames}
    inputs = {"video_path": video_path, "output_dir": str(output_dir)}
    return await _submit_single_stage(
        session,
        tenant_id=tenant_id,
        project_id=project_id,
        recipe="video_frames",
        kind="video_frames",
        inputs=inputs,
        spec=spec,
        inline=inline,
    )


async def submit_kapture_import(
    session: AsyncSession,
    *,
    tenant_id: str,
    project_id: str,
    archive_path: str,
    inline: bool = False,
) -> tuple[str, list[Any]]:
    """Parse a Kapture archive (extracted directory) into ``sensors``
    and ``records`` lists in the task result. The client follows up
    with a ``POST /v1/projects/{pid}/datasets`` of kind=``local``
    pointing at the returned ``image_root``."""
    require_capability("import.kapture")
    await project_service.get_project(session, tenant_id=tenant_id, project_id=project_id)
    spec: dict[str, Any] = {}
    inputs = {"archive_path": archive_path}
    return await _submit_single_stage(
        session,
        tenant_id=tenant_id,
        project_id=project_id,
        recipe="kapture_import",
        kind="kapture_import",
        inputs=inputs,
        spec=spec,
        inline=inline,
    )


async def submit_dataset_from_archive(
    session: AsyncSession,
    *,
    tenant_id: str,
    project_id: str,
    blob_sha: str,
    name: str | None = None,
    camera_model: str = "SIMPLE_RADIAL",
    intrinsics_mode: str = "single_camera",
    is_spherical: bool = False,
    image_prefix: str | None = None,
    inline: bool = False,
) -> tuple[str, list[Any]]:
    """Decode an uploaded image zip (``blob_sha``) into a derived dataset.

    The zip itself rode the normal chunked-upload protocol; this only
    enqueues the unpack. The worker reads the archive straight from the
    blob store (pure-memory for the in-memory backend), enforces the
    uncompressed-size cap, extracts the image entries, and emits the
    generic ``derived_dataset`` block — the dispatcher then creates the
    ImageSource + Dataset + Image rows. ``image_prefix`` restricts the
    import to a zip subpath (e.g. ``south-building/images/``); when
    unset the worker auto-detects the common image directory."""
    require_capability("import.archive")
    await project_service.get_project(session, tenant_id=tenant_id, project_id=project_id)
    paths = Paths(get_settings())
    output_dir = paths.workspace_root / "_archive_import" / new_id()
    spec: dict[str, Any] = {
        "name": name,
        "camera_model": camera_model,
        "intrinsics_mode": intrinsics_mode,
        "is_spherical": is_spherical,
    }
    if image_prefix:
        spec["image_prefix"] = image_prefix
    inputs = {"blob_sha": blob_sha, "output_dir": str(output_dir)}
    return await _submit_single_stage(
        session,
        tenant_id=tenant_id,
        project_id=project_id,
        recipe="import_archive",
        kind="import_archive",
        inputs=inputs,
        spec=spec,
        inline=inline,
    )


async def submit_vlad_index(
    session: AsyncSession,
    *,
    tenant_id: str,
    dataset_id: str,
    spec: dict[str, Any] | None = None,
    provider: str | None = None,
    inline: bool = False,
) -> tuple[str, list[Any], str | None]:
    """Build a VLAD descriptor index for the dataset (worker job).

    Returns ``(job_id, tasks, resolved_provider)``."""
    require_capability("similarity.vlad")
    spec = dict(spec or {})
    if provider is not None and "provider" not in spec:
        spec["provider"] = provider
    d = await dataset_service.get_dataset(session, tenant_id=tenant_id, dataset_id=dataset_id)
    provider_routing_service.apply_provider_resolution(
        spec,
        stage="vlad_index",
        capability="similarity.vlad",
        project_id=d.project_id,
        workspace=_routing_workspace(),
    )
    materialization = await derive_materialization(session, tenant_id=tenant_id, dataset=d)
    rows = (
        await session.execute(
            select(Image.image_id, Image.name).where(
                Image.tenant_id == tenant_id, Image.dataset_id == d.dataset_id
            )
        )
    ).all()
    image_id_by_name = {name: image_id for image_id, name in rows}
    paths = Paths(get_settings())
    dataset_dir = paths.dataset_root(tenant_id, d.project_id, d.dataset_id)
    inputs = {
        "dataset_id": d.dataset_id,
        "manifest_hash": d.manifest_hash,
        "materialization": materialization,
        "image_id_by_name": image_id_by_name,
        "dataset_dir": str(dataset_dir),
    }
    job_id, tasks = await _submit_single_stage(
        session,
        tenant_id=tenant_id,
        project_id=d.project_id,
        recipe="vlad_index",
        kind="vlad_index",
        inputs=inputs,
        spec=spec,
        inline=inline,
    )
    return job_id, tasks, spec.get("provider")


# Feature-database stages, table-driven: every entry submits through
# `_submit_dataset_db_stage`, which owns the shared body (capability
# gate, provider resolution, database-path + dataset-dir inputs). The
# public `submit_*` wrappers below stay as stable named exports.
_DATASET_DB_STAGES: dict[str, _StageDef] = {
    "vocab_tree": _StageDef(kind="vocab_tree", capability="index.vocab_tree"),
    "configure_rig": _StageDef(kind="configure_rig", capability="rigs.configure"),
    "two_view": _StageDef(kind="two_view", capability="geometry.two_view"),
}


async def _submit_dataset_db_stage(
    session: AsyncSession,
    *,
    tenant_id: str,
    dataset_id: str,
    recipe: str,
    spec: dict[str, Any],
    inline: bool = False,
) -> tuple[str, list[Any]]:
    """Capability-gate, resolve the provider, and submit a single
    dataset-scoped stage that operates on the dataset's feature
    database. ``recipe`` must be a ``_DATASET_DB_STAGES`` key; the
    stage's ``kind`` / ``capability`` constants come from that table.

    ``dataset_dir`` is always included so a stage that emits a sidecar
    (``vocab_tree``) has a stable home; stages that don't (``two_view``,
    ``configure_rig``) simply ignore it.

    ``spec`` is mutated in place by ``apply_provider_resolution`` so the
    caller (the route) can echo the resolved ``provider`` on the 202."""
    stage = _DATASET_DB_STAGES[recipe]
    capability = stage.resolve_capability(spec)
    require_capability(capability)
    d = await dataset_service.get_dataset(session, tenant_id=tenant_id, dataset_id=dataset_id)
    r, db_path = await _resolve_database_path(session, tenant_id=tenant_id, dataset=d, spec={})
    provider_routing_service.apply_provider_resolution(
        spec,
        stage=recipe,
        capability=capability,
        project_id=d.project_id,
        workspace=_routing_workspace(),
    )
    dataset_dir = Paths(get_settings()).dataset_root(tenant_id, d.project_id, d.dataset_id)
    inputs: dict[str, Any] = {
        "dataset_id": d.dataset_id,
        "recon_id": r.recon_id,
        "database_path": db_path,
        "dataset_dir": str(dataset_dir),
    }
    return await _submit_single_stage(
        session,
        tenant_id=tenant_id,
        project_id=d.project_id,
        recipe=recipe,
        kind=stage.kind,
        inputs=inputs,
        spec=spec,
        inline=inline,
    )


async def submit_build_vocab_tree(
    session: AsyncSession,
    *,
    tenant_id: str,
    dataset_id: str,
    spec: dict[str, Any],
    inline: bool = False,
) -> tuple[str, list[Any]]:
    """Build a reusable vocabulary-tree retrieval index for a dataset."""
    return await _submit_dataset_db_stage(
        session,
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        recipe="vocab_tree",
        spec=spec,
        inline=inline,
    )


async def submit_configure_rig(
    session: AsyncSession,
    *,
    tenant_id: str,
    dataset_id: str,
    spec: dict[str, Any],
    inline: bool = False,
) -> tuple[str, list[Any]]:
    """Declare or calibrate a multi-camera rig over a dataset's feature DB."""
    return await _submit_dataset_db_stage(
        session,
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        recipe="configure_rig",
        spec=spec,
        inline=inline,
    )


async def submit_estimate_two_view(
    session: AsyncSession,
    *,
    tenant_id: str,
    dataset_id: str,
    spec: dict[str, Any],
    inline: bool = False,
) -> tuple[str, list[Any]]:
    """Estimate two-view geometry (E/F/H + relative pose) for image pairs."""
    return await _submit_dataset_db_stage(
        session,
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        recipe="two_view",
        spec=spec,
        inline=inline,
    )
