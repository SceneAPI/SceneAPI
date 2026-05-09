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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters import backend_config
from app.core.capabilities import require as require_capability
from app.core.config import get_settings
from app.core.errors import ValidationError
from app.core.hashing import canonical_json, content_address
from app.core.ids import new_id
from app.core.paths import Paths
from app.db.models import Blob, Dataset, Image, ImageSource, Reconstruction
from app.orchestrator.dag import TaskNode, hash_inputs, hash_params
from app.orchestrator.scheduler import submit_job_dag
from app.services import dataset_service, reconstruction_service, runtime_version_service


def _stage_node(
    *,
    kind: str,
    inputs: dict,
    spec: dict,
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
    inputs: dict,
    spec: dict,
    inline: bool = False,
) -> tuple[str, list]:
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
    spec: dict,
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
) -> dict:
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
    out: dict = {
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


def _stage_backend_options(spec: dict, *, stage: str) -> dict:
    options = spec.get("backend_options") or {}
    if not isinstance(options, dict):
        raise ValidationError(f"{stage}.backend_options must be an object")
    return options


def validate_features_config(spec: dict) -> None:
    feature_type = str(spec.get("type") or "sift")
    backend_config.validate_backend_options(
        stage="features",
        capability=f"features.extract.{feature_type}",
        provider=spec.get("provider"),
        options=_stage_backend_options(spec, stage="features"),
    )


def validate_matches_config(spec: dict) -> None:
    pairs = spec.get("pairs", {})
    matcher = spec.get("matcher", {})
    if not isinstance(pairs, dict):
        raise ValidationError("spec.pairs must be a dict")
    if not isinstance(matcher, dict):
        raise ValidationError("spec.matcher must be a dict")
    strategy = str(pairs.get("strategy") or "exhaustive")
    matcher_type = str(matcher.get("type") or "nn-mutual")
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


def validate_verify_config(spec: dict) -> None:
    backend_config.validate_backend_options(
        stage="verify",
        capability="matches.verify",
        provider=spec.get("provider"),
        options=_stage_backend_options(spec, stage="verify"),
    )


def validate_mapping_config(spec: dict) -> None:
    kind = str(spec.get("kind") or "incremental")
    backend_config.validate_backend_options(
        stage="mapping",
        capability=f"map.{kind}",
        provider=spec.get("provider"),
        options=_stage_backend_options(spec, stage="mapping"),
    )


def validate_recipe_stage_configs(
    *,
    features_spec: dict,
    matches_spec: dict,
    verify_spec: dict,
    pipeline_spec: dict,
) -> None:
    validate_features_config(features_spec)
    validate_matches_config(matches_spec)
    validate_verify_config(verify_spec)
    validate_mapping_config(pipeline_spec)


async def submit_features(
    session: AsyncSession,
    *,
    tenant_id: str,
    dataset_id: str,
    spec: dict,
    inline: bool = False,
) -> tuple[str, list]:
    validate_features_config(spec)
    d = await dataset_service.get_dataset(session, tenant_id=tenant_id, dataset_id=dataset_id)
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
    spec: dict,
) -> tuple[Reconstruction, str]:
    r = await ensure_reconstruction(session, tenant_id=tenant_id, dataset=dataset, spec={})
    return r, reconstruction_database_path(tenant_id, dataset.project_id, r.recon_id)


async def submit_matches(
    session: AsyncSession,
    *,
    tenant_id: str,
    dataset_id: str,
    spec: dict,
    inline: bool = False,
) -> tuple[str, list]:
    validate_matches_config(spec)
    d = await dataset_service.get_dataset(session, tenant_id=tenant_id, dataset_id=dataset_id)
    pairs = spec.get("pairs", {})
    if not isinstance(pairs, dict):
        raise ValidationError("spec.pairs must be a dict")
    if pairs.get("strategy") == "vocabtree" and not pairs.get("vocab_tree_path"):
        raise ValidationError("pairs.vocab_tree_path is required for pairs.strategy=vocabtree")
    await _validate_explicit_pairs(session, tenant_id=tenant_id, dataset=d, pairs=pairs)
    r, db_path = await _resolve_database_path(session, tenant_id=tenant_id, dataset=d, spec=spec)
    inputs = {
        "dataset_id": d.dataset_id,
        "recon_id": r.recon_id,
        "manifest_hash": d.manifest_hash,
        "database_path": db_path,
    }
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
    pairs: dict,
) -> None:
    if pairs.get("strategy") != "explicit":
        if pairs.get("image_pairs") or pairs.get("pairs_blob_sha"):
            raise ValidationError(
                "pairs.image_pairs and pairs.pairs_blob_sha require pairs.strategy=explicit"
            )
        return

    image_pairs = pairs.get("image_pairs") or []
    pairs_blob_sha = pairs.get("pairs_blob_sha")
    has_inline = bool(image_pairs)
    has_blob = bool(pairs_blob_sha)
    if has_inline == has_blob:
        raise ValidationError(
            "pairs.strategy=explicit requires exactly one of pairs.image_pairs "
            "or pairs.pairs_blob_sha"
        )

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
            raise ValidationError(
                f"pairs.image_pairs[{index}] must reference two different images"
            )
        for image_name in (image_name1, image_name2):
            if image_name not in known_names:
                missing.append(image_name)
    if missing:
        missing_preview = ", ".join(sorted(set(missing))[:5])
        raise ValidationError(f"pairs.image_pairs references unknown dataset images: {missing_preview}")


async def submit_render_cubemap(
    session: AsyncSession,
    *,
    tenant_id: str,
    dataset_id: str,
    face_size: int | None = None,
    inline: bool = False,
) -> tuple[str, list]:
    """Render every spherical panorama into 6 cubemap faces.

    Refuses if the dataset isn't ``is_spherical=true``. The output is a
    directory under the dataset's workspace; the user can register it
    as a new ``local`` dataset for downstream pinhole-only pipelines.
    """
    require_capability("spherical.render_cubemap")
    d = await dataset_service.get_dataset(session, tenant_id=tenant_id, dataset_id=dataset_id)
    if not d.is_spherical:
        raise ValidationError("render_cubemap is only valid on datasets marked is_spherical=true")
    materialization = await derive_materialization(session, tenant_id=tenant_id, dataset=d)
    paths = Paths(get_settings())
    dataset_dir = paths.dataset_root(tenant_id, d.project_id, d.dataset_id)
    inputs = {
        "dataset_id": d.dataset_id,
        "materialization": materialization,
        "dataset_dir": str(dataset_dir),
    }
    spec: dict = {}
    if face_size:
        spec["face_size"] = int(face_size)
    return await _submit_single_stage(
        session,
        tenant_id=tenant_id,
        project_id=d.project_id,
        recipe="render_cubemap",
        kind="render_cubemap",
        inputs=inputs,
        spec=spec,
        inline=inline,
    )


async def submit_video_frames(
    session: AsyncSession,
    *,
    tenant_id: str,
    project_id: str,
    video_path: str,
    fps: float = 2.0,
    max_frames: int = 1000,
    inline: bool = False,
) -> tuple[str, list]:
    """Extract keyframes from a worker-local video file."""
    require_capability("video.frame_extract")
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
) -> tuple[str, list]:
    """Parse a Kapture archive (extracted directory) into ``sensors``
    and ``records`` lists in the task result. The client follows up
    with a ``POST /v1/projects/{pid}/datasets`` of kind=``local``
    pointing at the returned ``image_root``."""
    require_capability("import.kapture")
    spec: dict = {}
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


async def submit_merge_recons(
    session: AsyncSession,
    *,
    tenant_id: str,
    target_recon_id: str,
    source_recon_ids: list[str],
    sim3_aligners: list[dict] | None = None,
    inline: bool = False,
) -> tuple[str, list]:
    """Merge several reconstructions into ``target_recon_id``.

    All sources MUST belong to the same project as the target."""
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
    spec = {"sim3_aligners": sim3_aligners or []}
    inputs = {
        "target_recon_id": target.recon_id,
        "target_reconstruction_root": str(target_root),
        "source_sparse_dirs": source_dirs,
    }
    return await _submit_single_stage(
        session,
        tenant_id=tenant_id,
        project_id=target.project_id,
        recipe="merge_recons",
        kind="merge_recons",
        inputs=inputs,
        spec=spec,
        inline=inline,
    )


async def submit_to_cubemap(
    session: AsyncSession,
    *,
    tenant_id: str,
    recon_id: str,
    inline: bool = False,
) -> tuple[str, list]:
    """Convert a spherical reconstruction to a cubemap rig (worker job).

    Refuses if the reconstruction's dataset isn't marked
    ``is_spherical`` — this avoids running the converter against a
    pinhole reconstruction (which produces nonsense)."""
    require_capability("spherical.to_cubemap")
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
    spec: dict = {}
    return await _submit_single_stage(
        session,
        tenant_id=tenant_id,
        project_id=r.project_id,
        recipe="to_cubemap",
        kind="to_cubemap",
        inputs=inputs,
        spec=spec,
        inline=inline,
    )


async def submit_georegister(
    session: AsyncSession,
    *,
    tenant_id: str,
    recon_id: str,
    sim3: dict,
    inline: bool = False,
) -> tuple[str, list]:
    """Apply a Sim(3) georegistration transform to a reconstruction."""
    require_capability("georegister.sim3")
    r = await reconstruction_service.get_reconstruction(
        session, tenant_id=tenant_id, recon_id=recon_id
    )
    rec_root, sparse_dir = _reconstruction_paths(tenant_id, r)
    inputs = {
        "recon_id": r.recon_id,
        "reconstruction_root": str(rec_root),
        "sparse_dir": str(sparse_dir),
        "sim3": sim3,
    }
    spec = {"sim3": sim3}
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


async def submit_localize(
    session: AsyncSession,
    *,
    tenant_id: str,
    recon_id: str,
    blob_sha: str,
    spec: dict | None = None,
    inline: bool = False,
) -> tuple[str, list]:
    """Localize a single query image (`blob_sha`) against `recon_id`."""
    require_capability("localize.from_memory")
    spec = spec or {}
    r = await reconstruction_service.get_reconstruction(
        session, tenant_id=tenant_id, recon_id=recon_id
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
    spec: dict | None = None,
    inline: bool = False,
) -> tuple[str, list]:
    """Build a VLAD descriptor index for the dataset (worker job)."""
    require_capability("similarity.vlad")
    spec = spec or {}
    d = await dataset_service.get_dataset(session, tenant_id=tenant_id, dataset_id=dataset_id)
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
    return await _submit_single_stage(
        session,
        tenant_id=tenant_id,
        project_id=d.project_id,
        recipe="vlad_index",
        kind="vlad_index",
        inputs=inputs,
        spec=spec,
        inline=inline,
    )


async def submit_verify(
    session: AsyncSession,
    *,
    tenant_id: str,
    dataset_id: str,
    spec: dict,
    inline: bool = False,
) -> tuple[str, list]:
    validate_verify_config(spec)
    d = await dataset_service.get_dataset(session, tenant_id=tenant_id, dataset_id=dataset_id)
    r, db_path = await _resolve_database_path(session, tenant_id=tenant_id, dataset=d, spec=spec)
    inputs = {
        "dataset_id": d.dataset_id,
        "recon_id": r.recon_id,
        "manifest_hash": d.manifest_hash,
        "database_path": db_path,
    }
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
) -> dict[str, dict]:
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
    materialization: dict,
    database_path: str,
    features_spec: dict,
    matches_spec: dict,
    verify_spec: dict,
    pipeline_spec: dict,
    pose_priors: dict[str, dict] | None = None,
) -> list[TaskNode]:
    """Stitch extract → match → verify → map into one DAG. Each TaskNode
    is hashed with the same shape as a single-stage submission, so a
    recipe that re-uses an already-computed extract+match prefix
    short-circuits to the cached results.

    ``pose_priors`` are optional per-image priors (keyed by image name);
    when present they're forwarded into the map task's inputs so the
    worker can wire them into pycolmap's ``MappingInput``.
    """
    extract_inputs = {
        "project_id": project_id,
        "dataset_id": dataset_id,
        "recon_id": recon_id,
        "materialization": materialization,
        "database_path": database_path,
    }
    extract = _stage_node(kind="extract", inputs=extract_inputs, spec=features_spec)

    common_stage_inputs = {
        "recon_id": recon_id,
        "dataset_id": dataset_id,
        "database_path": database_path,
    }
    match = _stage_node(
        kind="match",
        inputs={**common_stage_inputs, "depends_on": extract.task_id},
        spec=matches_spec,
        depends_on=[extract.task_id],
    )
    verify = _stage_node(
        kind="verify",
        inputs={**common_stage_inputs, "depends_on": match.task_id},
        spec=verify_spec,
        depends_on=[match.task_id],
    )
    map_inputs = {
        "project_id": project_id,
        "recon_id": recon_id,
        "dataset_id": dataset_id,
        "database_path": database_path,
        "materialization": materialization,
        "depends_on": verify.task_id,
    }
    if pose_priors:
        map_inputs["pose_priors"] = pose_priors
    map_node = _stage_node(
        kind="map", inputs=map_inputs, spec=pipeline_spec, depends_on=[verify.task_id]
    )
    return [extract, match, verify, map_node]
