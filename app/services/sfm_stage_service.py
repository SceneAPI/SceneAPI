"""Build the Job -> Task DAG for SfM stage calls and named recipes.

The HTTP layer no longer needs to pass `image_root` / `image_list` /
`database_path` — they are derived here from the dataset's source and
the persisted Image rows. This keeps the API surface clean (the
client knows about its dataset, not about worker-side filesystem
layout) and ensures the same materialization logic is used for every
stage.

A *recipe* (`incremental | global | hierarchical | spherical`) is
sugar over the per-stage builders: it strings extract → match →
verify → map into one DAG so per-stage caching short-circuits as
soon as any prefix is reused.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters import backend_config
from app.core.capabilities import require as require_capability
from app.core.config import get_settings
from app.core.errors import ValidationError
from app.core.hashing import canonical_json, content_address
from app.core.ids import new_id
from app.core.paths import Paths
from app.core.projections import projection_capability
from app.db.models import Blob, Dataset, Image, ImageSource, Reconstruction
from app.orchestrator.dag import TaskNode, hash_inputs, hash_params
from app.orchestrator.scheduler import submit_job_dag
from app.schemas.api.projections import CubemapProjectionSpec, ProjectionJobRequest
from app.services import (
    artifact_service,
    dataset_service,
    project_service,
    provider_routing_service,
    reconstruction_service,
    runtime_version_service,
)


def _stage_node(
    *,
    kind: str,
    inputs: dict[str, Any],
    spec: dict[str, Any],
    depends_on: list[str] | None = None,
) -> TaskNode:
    """Cache-key parity is the reason this is shared: a single-stage
    submission and the same stage inside a recipe must produce the same
    `(inputs_hash, params_hash)` so a recipe rerun short-circuits."""
    return TaskNode(
        task_id=new_id(),
        kind=kind,
        inputs_hash=hash_inputs(inputs),
        params_hash=hash_params(spec),
        depends_on=depends_on or [],
        gpu_required=True,
        metadata={"inputs": inputs, "spec": spec},
    )


def _reconstruction_paths(tenant_id: str, r: Reconstruction) -> tuple[Path, Path]:
    """Return ``(rec_root, sparse_dir)`` for a reconstruction.

    ``sparse_dir`` is the canonical ``rec_root / "sparse"`` location
    that every stage past mapping reads / writes; this helper just
    saves the 3-line ``Paths(get_settings()) / reconstruction_root /
    sparse_dir`` trio that 5 ``submit_*`` callers used to recompute.
    """
    paths = Paths(get_settings())
    rec_root = paths.reconstruction_root(tenant_id, r.project_id, r.recon_id)
    return rec_root, rec_root / "sparse"


async def _submit_single_stage(
    session: AsyncSession,
    *,
    tenant_id: str,
    project_id: str,
    recipe: str,
    kind: str,
    inputs: dict[str, Any],
    spec: dict[str, Any],
    inline: bool = False,
) -> tuple[str, list[Any]]:
    """Single-task Job submit — every ``submit_*`` calls this with its
    pre-computed ``inputs`` / ``spec``.

    Centralises the ``[_stage_node(...)]`` + ``submit_job_dag(...)``
    tail so cross-cutting changes (priority, request-id propagation,
    capability auto-check) land in one place instead of 13. Keeps
    ``recipe`` and ``kind`` as separate parameters because they
    differ for the SfM stages (``recipe="features"`` /
    ``kind="extract"``, ``recipe="matches"`` / ``kind="match"``);
    callers that match (most non-stage submits) still pass them
    explicitly so the call sites stay self-documenting.
    """
    return await submit_job_dag(
        session,
        tenant_id=tenant_id,
        project_id=project_id,
        recipe=recipe,
        spec=spec,
        nodes=[_stage_node(kind=kind, inputs=inputs, spec=spec)],
        inline=inline,
    )


async def ensure_reconstruction(
    session: AsyncSession,
    *,
    tenant_id: str,
    dataset: Dataset,
    spec: dict[str, Any],
) -> Reconstruction:
    rv = await runtime_version_service.ensure_runtime_version(session)
    snap_hash = dataset.manifest_hash or ""
    spec_hash = content_address(canonical_json(spec))
    result = await session.execute(
        select(Reconstruction).where(
            Reconstruction.tenant_id == tenant_id,
            Reconstruction.dataset_id == dataset.dataset_id,
            Reconstruction.dataset_snapshot_hash == snap_hash,
            Reconstruction.rv_id == rv.rv_id,
        )
    )
    rows = list(result.scalars().all())
    for r in rows:
        if content_address(canonical_json(r.spec_json)) == spec_hash:
            return r
    r = Reconstruction(
        recon_id=new_id(),
        tenant_id=tenant_id,
        project_id=dataset.project_id,
        dataset_id=dataset.dataset_id,
        dataset_snapshot_hash=snap_hash,
        spec_json=spec,
        rv_id=rv.rv_id,
    )
    session.add(r)
    await session.flush()
    return r


async def derive_materialization(
    session: AsyncSession, *, tenant_id: str, dataset: Dataset
) -> dict[str, Any]:
    """Return a manifest the worker uses to realize the dataset on disk.

    Shape::

        {
          "kind": "upload" | "local" | "s3",
          "image_list": [str],            # ordered image names
          "image_root": str | None,       # filesystem path (None for upload)
          "blob_shas": {name: sha},       # only for upload
          "bucket": str, "prefix": str,   # only for s3
        }
    """
    src = await session.get(ImageSource, dataset.source_id)
    if src is None:
        raise ValidationError("dataset has no source row")
    rows = (
        await session.execute(
            select(Image.name, Image.content_sha, Image.rel_path)
            .where(Image.tenant_id == tenant_id, Image.dataset_id == dataset.dataset_id)
            .order_by(Image.name)
        )
    ).all()
    image_list = [r[0] for r in rows]
    if not image_list:
        raise ValidationError(
            "dataset has no images registered; add some with "
            f"POST /v1/datasets/{dataset.dataset_id}/images first"
        )
    out: dict[str, Any] = {
        "kind": src.kind,
        "image_list": image_list,
    }
    if src.kind == "upload":
        out["blob_shas"] = {name: sha for name, sha, _ in rows}
        out["image_root"] = None
    elif src.kind == "local":
        if not src.uri_or_root:
            raise ValidationError("local source missing root path")
        out["image_root"] = src.uri_or_root
    elif src.kind == "s3":
        if not src.uri_or_root or not src.uri_or_root.startswith("s3://"):
            raise ValidationError("s3 source missing s3://bucket/prefix uri")
        bucket_prefix = src.uri_or_root[len("s3://") :]
        bucket, _, prefix = bucket_prefix.partition("/")
        out["bucket"] = bucket
        out["prefix"] = prefix
        out["image_root"] = None
    else:
        raise ValidationError(f"unsupported source kind: {src.kind}")
    return out


def reconstruction_database_path(tenant_id: str, project_id: str, recon_id: str) -> str:
    paths = Paths(get_settings())
    return str(paths.reconstruction_root(tenant_id, project_id, recon_id) / "database.db")


def _stage_backend_options(spec: dict[str, Any], *, stage: str) -> dict[str, Any]:
    options = spec.get("backend_options") or {}
    if not isinstance(options, dict):
        raise ValidationError(f"{stage}.backend_options must be an object")
    return options


def _merge_spec_input_artifacts(*sources: object) -> dict[str, object]:
    merged: dict[str, object] = {}
    for source in sources:
        if source is None:
            continue
        if not isinstance(source, dict):
            raise ValidationError("input_artifacts must be an object")
        merged.update(source)
    return merged


def _routing_workspace() -> str:
    return str(get_settings().workspace_root)


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


async def _resolve_database_path(
    session: AsyncSession,
    *,
    tenant_id: str,
    dataset: Dataset,
    spec: dict[str, Any],
) -> tuple[Reconstruction, str]:
    r = await ensure_reconstruction(session, tenant_id=tenant_id, dataset=dataset, spec={})
    return r, reconstruction_database_path(tenant_id, dataset.project_id, r.recon_id)


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


async def submit_merge_recons(
    session: AsyncSession,
    *,
    tenant_id: str,
    target_recon_id: str,
    source_recon_ids: list[str],
    sim3_aligners: list[dict[str, Any]] | None = None,
    provider: str | None = None,
    inline: bool = False,
) -> tuple[str, list[Any], str | None]:
    """Merge several reconstructions into ``target_recon_id``.

    All sources MUST belong to the same project as the target. Returns
    ``(job_id, tasks, resolved_provider)`` — the third element is the
    provider routing actually selected, which the route echoes on the
    202 (it may differ from the request when a routing profile fired)."""
    require_capability("recon.merge")
    target = await reconstruction_service.get_reconstruction(
        session, tenant_id=tenant_id, recon_id=target_recon_id
    )
    paths = Paths(get_settings())
    target_root = paths.reconstruction_root(tenant_id, target.project_id, target.recon_id)
    source_dirs: list[str] = []
    for rid in source_recon_ids:
        r = await reconstruction_service.get_reconstruction(
            session, tenant_id=tenant_id, recon_id=rid
        )
        if r.project_id != target.project_id:
            raise ValidationError(
                f"merge: source recon {rid} is in a different project than target"
            )
        rec_root = paths.reconstruction_root(tenant_id, r.project_id, r.recon_id)
        source_dirs.append(str(rec_root / "sparse"))
    spec: dict[str, Any] = {"sim3_aligners": sim3_aligners or []}
    if provider is not None:
        spec["provider"] = provider
    provider_routing_service.apply_provider_resolution(
        spec,
        stage="merge_recons",
        capability="recon.merge",
        project_id=target.project_id,
        workspace=_routing_workspace(),
    )
    inputs = {
        "target_recon_id": target.recon_id,
        "target_reconstruction_root": str(target_root),
        "source_sparse_dirs": source_dirs,
    }
    job_id, tasks = await _submit_single_stage(
        session,
        tenant_id=tenant_id,
        project_id=target.project_id,
        recipe="merge_recons",
        kind="merge_recons",
        inputs=inputs,
        spec=spec,
        inline=inline,
    )
    return job_id, tasks, spec.get("provider")


async def submit_to_cubemap(
    session: AsyncSession,
    *,
    tenant_id: str,
    recon_id: str,
    provider: str | None = None,
    inline: bool = False,
) -> tuple[str, list[Any], str | None]:
    """Convert a spherical reconstruction to a cubemap rig (worker job).

    Refuses if the reconstruction's dataset isn't marked
    ``is_spherical`` — this avoids running the converter against a
    pinhole reconstruction (which produces nonsense). Returns
    ``(job_id, tasks, resolved_provider)``."""
    require_capability("projection.cubemap_rig")
    r = await reconstruction_service.get_reconstruction(
        session, tenant_id=tenant_id, recon_id=recon_id
    )
    d = await dataset_service.get_dataset(session, tenant_id=tenant_id, dataset_id=r.dataset_id)
    if not d.is_spherical:
        raise ValidationError("to_cubemap is only valid on datasets marked is_spherical=true")
    materialization = await derive_materialization(session, tenant_id=tenant_id, dataset=d)
    image_root = materialization.get("image_root")
    if not image_root:
        raise ValidationError(
            "to_cubemap needs a local image_root; upload sources are not "
            "supported yet (worker can't materialize on demand here)"
        )
    rec_root, sparse_dir = _reconstruction_paths(tenant_id, r)
    inputs = {
        "recon_id": r.recon_id,
        "reconstruction_root": str(rec_root),
        "sparse_dir": str(sparse_dir),
        "image_root": image_root,
    }
    spec: dict[str, Any] = {}
    if provider is not None:
        spec["provider"] = provider
    provider_routing_service.apply_provider_resolution(
        spec,
        stage="to_cubemap",
        capability="projection.cubemap_rig",
        project_id=r.project_id,
        workspace=_routing_workspace(),
    )
    job_id, tasks = await _submit_single_stage(
        session,
        tenant_id=tenant_id,
        project_id=r.project_id,
        recipe="to_cubemap",
        kind="to_cubemap",
        inputs=inputs,
        spec=spec,
        inline=inline,
    )
    return job_id, tasks, spec.get("provider")


async def submit_georegister(
    session: AsyncSession,
    *,
    tenant_id: str,
    recon_id: str,
    spec: dict[str, Any],
    inline: bool = False,
) -> tuple[str, list[Any]]:
    """Georegister a reconstruction.

    ``spec["mode"]`` selects the path: ``sim3`` applies the supplied
    ``spec["sim3"]`` transform (capability ``georegister.sim3``);
    ``gps`` solves the transform from georeferenced inputs (capability
    ``georegister.gps``). The worker reads ``spec["mode"]`` via
    :func:`backend_for_stage` and dispatches to the matching backend
    method.
    """
    mode = str(spec.get("mode") or "sim3")
    capability = "georegister.gps" if mode == "gps" else "georegister.sim3"
    require_capability(capability)
    r = await reconstruction_service.get_reconstruction(
        session, tenant_id=tenant_id, recon_id=recon_id
    )
    provider_routing_service.apply_provider_resolution(
        spec,
        stage="georegister",
        capability=capability,
        project_id=r.project_id,
        workspace=_routing_workspace(),
    )
    rec_root, sparse_dir = _reconstruction_paths(tenant_id, r)
    inputs: dict[str, Any] = {
        "recon_id": r.recon_id,
        "reconstruction_root": str(rec_root),
        "sparse_dir": str(sparse_dir),
    }
    if spec.get("sim3"):
        inputs["sim3"] = spec["sim3"]
    return await _submit_single_stage(
        session,
        tenant_id=tenant_id,
        project_id=r.project_id,
        recipe="georegister",
        kind="georegister",
        inputs=inputs,
        spec=spec,
        inline=inline,
    )


async def _recon_stage_base(
    session: AsyncSession,
    *,
    tenant_id: str,
    recon_id: str,
    needs_images: bool = False,
) -> tuple[Reconstruction, dict[str, Any]]:
    """Resolve a reconstruction and build the ``inputs`` block shared by
    every reconstruction-scoped portable stage (``ba`` / ``triangulate``
    / ``pgo`` / ``export`` / ``relocalize`` / ``undistort``).

    ``model_path`` is the live ``sparse/`` dir; the worker derives its
    own ``output_path`` from ``reconstruction_root`` + ``task_id`` so
    the cache key stays stable across re-submits. When ``needs_images``
    the stage also gets a local ``image_root`` + ``database_path`` —
    upload sources are rejected here (the worker can't materialize on
    demand for these stages).
    """
    r = await reconstruction_service.get_reconstruction(
        session, tenant_id=tenant_id, recon_id=recon_id
    )
    rec_root, sparse_dir = _reconstruction_paths(tenant_id, r)
    inputs: dict[str, Any] = {
        "recon_id": r.recon_id,
        "reconstruction_root": str(rec_root),
        "model_path": str(sparse_dir),
    }
    if needs_images:
        d = await dataset_service.get_dataset(session, tenant_id=tenant_id, dataset_id=r.dataset_id)
        materialization = await derive_materialization(session, tenant_id=tenant_id, dataset=d)
        image_root = materialization.get("image_root")
        if not image_root:
            raise ValidationError(
                "this stage needs a local image_root; upload sources are not "
                "supported here (the worker can't materialize on demand)"
            )
        inputs["image_root"] = image_root
        inputs["database_path"] = reconstruction_database_path(tenant_id, r.project_id, r.recon_id)
    return r, inputs


async def _submit_recon_stage(
    session: AsyncSession,
    *,
    tenant_id: str,
    recon_id: str,
    recipe: str,
    kind: str,
    capability: str,
    spec: dict[str, Any],
    needs_images: bool = False,
    inline: bool = False,
) -> tuple[str, list[Any]]:
    """Capability-gate, resolve the provider, and submit a single
    reconstruction-scoped stage task. ``require_capability`` runs first
    so an unbacked deployment 501s before any DB work.

    ``spec`` is mutated in place by ``apply_provider_resolution`` so the
    caller (the route) can echo the resolved ``provider`` on the 202."""
    require_capability(capability)
    r, inputs = await _recon_stage_base(
        session, tenant_id=tenant_id, recon_id=recon_id, needs_images=needs_images
    )
    provider_routing_service.apply_provider_resolution(
        spec,
        stage=recipe,
        capability=capability,
        project_id=r.project_id,
        workspace=_routing_workspace(),
    )
    return await _submit_single_stage(
        session,
        tenant_id=tenant_id,
        project_id=r.project_id,
        recipe=recipe,
        kind=kind,
        inputs=inputs,
        spec=spec,
        inline=inline,
    )


def _bundle_adjust_capability(spec: dict[str, Any]) -> str:
    mode = str(spec.get("mode") or "standard")
    return {
        "standard": "ba.standard",
        "two_stage": "ba.two_stage",
        "featuremetric": "ba.featuremetric",
        "rig": "ba.rig",
    }.get(mode, "ba.standard")


async def submit_bundle_adjust(
    session: AsyncSession,
    *,
    tenant_id: str,
    recon_id: str,
    spec: dict[str, Any],
    inline: bool = False,
) -> tuple[str, list[Any]]:
    """Standalone bundle adjustment over a reconstruction's sparse model."""
    return await _submit_recon_stage(
        session,
        tenant_id=tenant_id,
        recon_id=recon_id,
        recipe="bundle_adjust",
        kind="ba",
        capability=_bundle_adjust_capability(spec),
        spec=spec,
        inline=inline,
    )


async def submit_triangulate(
    session: AsyncSession,
    *,
    tenant_id: str,
    recon_id: str,
    spec: dict[str, Any],
    inline: bool = False,
) -> tuple[str, list[Any]]:
    """Re-triangulate a reconstruction against its feature database."""
    return await _submit_recon_stage(
        session,
        tenant_id=tenant_id,
        recon_id=recon_id,
        recipe="triangulate",
        kind="triangulate",
        capability="triangulate.retri",
        spec=spec,
        needs_images=True,
        inline=inline,
    )


async def submit_pose_graph_optimize(
    session: AsyncSession,
    *,
    tenant_id: str,
    recon_id: str,
    spec: dict[str, Any],
    inline: bool = False,
) -> tuple[str, list[Any]]:
    """Pose-graph optimization over a reconstruction."""
    return await _submit_recon_stage(
        session,
        tenant_id=tenant_id,
        recon_id=recon_id,
        recipe="pose_graph_optimize",
        kind="pgo",
        capability="pgo.optimize",
        spec=spec,
        inline=inline,
    )


async def submit_export(
    session: AsyncSession,
    *,
    tenant_id: str,
    recon_id: str,
    spec: dict[str, Any],
    inline: bool = False,
) -> tuple[str, list[Any]]:
    """Export a reconstruction's sparse model to a portable format."""
    fmt = str(spec.get("format") or "ply")
    return await _submit_recon_stage(
        session,
        tenant_id=tenant_id,
        recon_id=recon_id,
        recipe="export",
        kind="export",
        capability=f"export.{fmt}",
        spec=spec,
        inline=inline,
    )


async def submit_relocalize(
    session: AsyncSession,
    *,
    tenant_id: str,
    recon_id: str,
    spec: dict[str, Any],
    inline: bool = False,
) -> tuple[str, list[Any]]:
    """Register additional images into an existing reconstruction."""
    return await _submit_recon_stage(
        session,
        tenant_id=tenant_id,
        recon_id=recon_id,
        recipe="relocalize",
        kind="relocalize",
        capability="relocalize.images",
        spec=spec,
        needs_images=True,
        inline=inline,
    )


async def submit_undistort(
    session: AsyncSession,
    *,
    tenant_id: str,
    recon_id: str,
    spec: dict[str, Any],
    inline: bool = False,
) -> tuple[str, list[Any]]:
    """Undistort a reconstruction's images + emit adjusted intrinsics."""
    return await _submit_recon_stage(
        session,
        tenant_id=tenant_id,
        recon_id=recon_id,
        recipe="undistort",
        kind="undistort",
        capability="image.undistort",
        spec=spec,
        needs_images=True,
        inline=inline,
    )


async def _submit_dataset_db_stage(
    session: AsyncSession,
    *,
    tenant_id: str,
    dataset_id: str,
    recipe: str,
    kind: str,
    capability: str,
    spec: dict[str, Any],
    inline: bool = False,
) -> tuple[str, list[Any]]:
    """Capability-gate, resolve the provider, and submit a single
    dataset-scoped stage that operates on the dataset's feature
    database (``vocab_tree`` / ``configure_rig`` / ``two_view``).

    ``dataset_dir`` is always included so a stage that emits a sidecar
    (``vocab_tree``) has a stable home; stages that don't (``two_view``,
    ``configure_rig``) simply ignore it.

    ``spec`` is mutated in place by ``apply_provider_resolution`` so the
    caller (the route) can echo the resolved ``provider`` on the 202."""
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
        kind=kind,
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
        kind="vocab_tree",
        capability="index.vocab_tree",
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
        kind="configure_rig",
        capability="rigs.configure",
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
        kind="two_view",
        capability="geometry.two_view",
        spec=spec,
        inline=inline,
    )


async def submit_localize(
    session: AsyncSession,
    *,
    tenant_id: str,
    recon_id: str,
    blob_sha: str,
    spec: dict[str, Any] | None = None,
    inline: bool = False,
) -> tuple[str, list[Any]]:
    """Localize a single query image (`blob_sha`) against `recon_id`.

    ``spec`` may carry a ``provider`` key; the worker reads it via
    ``backend_for_stage(spec)`` to route the call to a specific backend.
    """
    require_capability("localize.from_memory")
    spec = spec if spec is not None else {}
    r = await reconstruction_service.get_reconstruction(
        session, tenant_id=tenant_id, recon_id=recon_id
    )
    provider_routing_service.apply_provider_resolution(
        spec,
        stage="localize",
        capability="localize.from_memory",
        project_id=r.project_id,
        workspace=_routing_workspace(),
    )
    _, sparse_dir = _reconstruction_paths(tenant_id, r)
    inputs = {
        "recon_id": r.recon_id,
        "sparse_dir": str(sparse_dir),
        "blob_sha": blob_sha,
    }
    return await _submit_single_stage(
        session,
        tenant_id=tenant_id,
        project_id=r.project_id,
        recipe="localize",
        kind="localize",
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
            "depends_on": extract.task_id,
            **({"input_artifacts": input_artifacts} if input_artifacts else {}),
        },
        spec=matches_spec,
        depends_on=[extract.task_id],
    )
    verify = _stage_node(
        kind="verify",
        inputs={
            **common_stage_inputs,
            "database_path": matches_db_path or database_path,
            "depends_on": match.task_id,
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
        "depends_on": verify.task_id,
    }
    if pose_priors:
        map_inputs["pose_priors"] = pose_priors
    if input_artifacts:
        map_inputs["input_artifacts"] = input_artifacts
    map_node = _stage_node(
        kind="map", inputs=map_inputs, spec=pipeline_spec, depends_on=[verify.task_id]
    )
    return [extract, match, verify, map_node]
