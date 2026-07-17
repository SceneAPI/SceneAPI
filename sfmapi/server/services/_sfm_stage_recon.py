"""Reconstruction-scoped stage submits for the ``sfm_stage_service`` facade.

The portable recon stages (``ba`` / ``triangulate`` / ``pgo`` /
``export`` / ``relocalize`` / ``undistort``) are table-driven: their
per-stage constants live in ``_RECON_STAGES`` and every public
``submit_*`` wrapper routes through ``_submit_registered_recon_stage``.
The recon-scoped utilities (``localize`` / ``merge_recons`` /
``to_cubemap`` / ``georegister``) have bespoke inputs and keep explicit
bodies.

Import through :mod:`sfmapi.server.services.sfm_stage_service`; this
underscore module is an internal layout detail.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from sfmapi.server.core.capabilities import require as require_capability
from sfmapi.server.core.config import get_settings
from sfmapi.server.core.errors import ValidationError
from sfmapi.server.core.paths import Paths
from sfmapi.server.db.models import Reconstruction
from sfmapi.server.schemas.pipeline_spec import BA_MODE_CAPABILITIES
from sfmapi.server.services import (
    dataset_service,
    provider_routing_service,
    reconstruction_service,
)
from sfmapi.server.services._sfm_stage_core import (
    _reconstruction_paths,
    _routing_workspace,
    _StageDef,
    _submit_single_stage,
    derive_materialization,
    reconstruction_database_path,
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
    return BA_MODE_CAPABILITIES.get(mode, "ba.standard")


def _export_capability(spec: dict[str, Any]) -> str:
    fmt = str(spec.get("format") or "ply")
    return f"export.{fmt}"


# Portable recon stages, table-driven: recipe -> per-stage constants.
# The public `submit_*` wrappers below stay as stable named exports with
# unchanged signatures; only the constants live here.
_RECON_STAGES: dict[str, _StageDef] = {
    "bundle_adjust": _StageDef(kind="ba", capability=_bundle_adjust_capability),
    "triangulate": _StageDef(kind="triangulate", capability="triangulate.retri", needs_images=True),
    "pose_graph_optimize": _StageDef(kind="pgo", capability="pgo.optimize"),
    "export": _StageDef(kind="export", capability=_export_capability),
    "relocalize": _StageDef(kind="relocalize", capability="relocalize.images", needs_images=True),
    "undistort": _StageDef(kind="undistort", capability="image.undistort", needs_images=True),
}


async def _submit_registered_recon_stage(
    session: AsyncSession,
    *,
    tenant_id: str,
    recon_id: str,
    recipe: str,
    spec: dict[str, Any],
    inline: bool = False,
) -> tuple[str, list[Any]]:
    """Submit a recon stage whose constants live in ``_RECON_STAGES``."""
    stage = _RECON_STAGES[recipe]
    return await _submit_recon_stage(
        session,
        tenant_id=tenant_id,
        recon_id=recon_id,
        recipe=recipe,
        kind=stage.kind,
        capability=stage.resolve_capability(spec),
        spec=spec,
        needs_images=stage.needs_images,
        inline=inline,
    )


async def submit_bundle_adjust(
    session: AsyncSession,
    *,
    tenant_id: str,
    recon_id: str,
    spec: dict[str, Any],
    inline: bool = False,
) -> tuple[str, list[Any]]:
    """Standalone bundle adjustment over a reconstruction's sparse model."""
    return await _submit_registered_recon_stage(
        session,
        tenant_id=tenant_id,
        recon_id=recon_id,
        recipe="bundle_adjust",
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
    return await _submit_registered_recon_stage(
        session,
        tenant_id=tenant_id,
        recon_id=recon_id,
        recipe="triangulate",
        spec=spec,
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
    return await _submit_registered_recon_stage(
        session,
        tenant_id=tenant_id,
        recon_id=recon_id,
        recipe="pose_graph_optimize",
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
    return await _submit_registered_recon_stage(
        session, tenant_id=tenant_id, recon_id=recon_id, recipe="export", spec=spec, inline=inline
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
    return await _submit_registered_recon_stage(
        session,
        tenant_id=tenant_id,
        recon_id=recon_id,
        recipe="relocalize",
        spec=spec,
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
    return await _submit_registered_recon_stage(
        session,
        tenant_id=tenant_id,
        recon_id=recon_id,
        recipe="undistort",
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
