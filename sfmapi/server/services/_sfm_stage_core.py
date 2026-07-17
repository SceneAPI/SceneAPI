"""Shared core for the ``sfm_stage_service`` module family.

``_stage_node`` and ``_submit_single_stage`` are the cache-key-bearing
primitives every stage submit funnels through (decision register L30);
the rest are the materialization / path / spec helpers shared by the
dataset-stage (``_sfm_stage_dataset``), recon-stage
(``_sfm_stage_recon``), and recipe (``_sfm_stage_recipes``) modules.
Nothing here knows any concrete stage — per-stage constants live in the
``_StageDef`` registries of the domain modules.

Import this family through the :mod:`sfmapi.server.services.sfm_stage_service`
facade; these underscore modules are an internal layout detail.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sfmapi.server.core.config import get_settings
from sfmapi.server.core.errors import ValidationError
from sfmapi.server.core.hashing import canonical_json, content_address
from sfmapi.server.core.ids import new_id
from sfmapi.server.core.path_safety import validate_safe_relative_path
from sfmapi.server.core.paths import Paths
from sfmapi.server.db.models import Dataset, Image, ImageSource, Reconstruction
from sfmapi.server.orchestrator.dag import TaskNode, hash_inputs, hash_params
from sfmapi.server.orchestrator.scheduler import submit_job_dag
from sfmapi.server.services import runtime_version_service


@dataclass(frozen=True)
class _StageDef:
    """Constants for one table-driven stage submit.

    The public ``submit_*`` wrappers stay as named exports with stable
    signatures; everything that used to vary between their bodies
    (worker task ``kind``, gating ``capability``, whether the stage
    needs a local image root) lives here instead, keyed by ``recipe``
    in the per-domain registries.

    ``capability`` is either a literal capability id or a
    ``spec -> capability`` resolver for stages whose capability depends
    on the request (bundle adjust mode, export format).
    """

    kind: str
    capability: str | Callable[[dict[str, Any]], str]
    needs_images: bool = False

    def resolve_capability(self, spec: dict[str, Any]) -> str:
        capability = self.capability
        return capability(spec) if callable(capability) else capability


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
          "rel_paths": {name: rel_path},  # only for local
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
        out["rel_paths"] = {
            validate_safe_relative_path(name, field="image name"): validate_safe_relative_path(
                rel_path or name, field="rel_path"
            )
            for name, _, rel_path in rows
        }
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


async def _resolve_database_path(
    session: AsyncSession,
    *,
    tenant_id: str,
    dataset: Dataset,
    spec: dict[str, Any],
) -> tuple[Reconstruction, str]:
    r = await ensure_reconstruction(session, tenant_id=tenant_id, dataset=dataset, spec={})
    return r, reconstruction_database_path(tenant_id, dataset.project_id, r.recon_id)
