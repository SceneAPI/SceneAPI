"""Reconstruction + Submodel reads + snapshot endpoints."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1._helpers import accepted_response
from app.api.v1.artifacts import artifact_out
from app.core.errors import NotFoundError, ValidationError
from app.core.http import file_etag, if_none_match_hit, not_modified, weak_etag
from app.core.paths import Paths
from app.core.tenancy import current_tenant
from app.db.session import get_db
from app.schemas.api.artifacts import StageArtifactOut
from app.schemas.api.common import Link, Page, to_out
from app.schemas.api.jobs import JobAcceptedResponse
from app.schemas.api.reconstructions import (
    ImageObservationsResponse,
    PointVisibilityResponse,
    ReconstructionOut,
    SnapshotListResponse,
    SubModelOut,
)
from app.schemas.pipeline_spec import (
    PROVIDER_SELECTOR_MAX_LENGTH,
    PROVIDER_SELECTOR_PATTERN,
)
from app.services import artifact_service, reconstruction_service, sfm_stage_service
from app.storage import observations as obs_store
from app.storage import tiles as tiles_store

router = APIRouter(tags=["reconstructions"])

_FILE_RESPONSE = {
    200: {
        "content": {
            "application/octet-stream": {"schema": {"type": "string", "format": "binary"}},
            "application/x-sfm-points-v1": {"schema": {"type": "string", "format": "binary"}},
            "application/json": {"schema": {"type": "object", "additionalProperties": True}},
        },
        "description": "Snapshot file bytes or JSON sidecar content.",
    }
}

_POINT_TILE_RESPONSE = {
    200: {
        "content": {
            "application/octet-stream": {"schema": {"type": "string", "format": "binary"}},
            "application/x-sfm-points-v1": {"schema": {"type": "string", "format": "binary"}},
        },
        "description": "Binary point tile bytes.",
    }
}

_JSON_FILE_RESPONSE = {
    200: {
        "content": {
            "application/json": {"schema": {"type": "object", "additionalProperties": True}}
        },
        "description": "JSON file content.",
    }
}


def _recon_links(recon_id: str) -> dict[str, Link]:
    return {
        "self": Link(href=f"/v1/reconstructions/{recon_id}"),
        "submodels": Link(href=f"/v1/reconstructions/{recon_id}/submodels"),
        "snapshots": Link(href=f"/v1/reconstructions/{recon_id}/snapshots"),
        "artifacts": Link(href=f"/v1/reconstructions/{recon_id}/artifacts"),
        "two_view_geometries": Link(
            href=f"/v1/reconstructions/{recon_id}/two_view_geometries.json"
        ),
        "correspondence_graph": Link(
            href=f"/v1/reconstructions/{recon_id}/correspondence_graph.json"
        ),
    }


def _submodel_links(sm: Any) -> dict[str, Link]:
    links = {
        "self": Link(href=f"/v1/submodels/{sm.submodel_id}"),
        "reconstruction": Link(href=f"/v1/reconstructions/{sm.recon_id}"),
    }
    if sm.snapshot_seq is not None:
        base = f"/v1/reconstructions/{sm.recon_id}/snapshots/{sm.snapshot_seq}/submodels/{sm.idx}"
        links.update(
            {
                "snapshot": Link(href=base),
                "points": Link(href=f"{base}/points.bin"),
                "preview": Link(href=f"{base}/points_preview.bin"),
                "cameras": Link(href=f"{base}/cameras.json"),
                "images": Link(href=f"{base}/images.json"),
                "rigs": Link(href=f"{base}/rigs.json"),
                "frames": Link(href=f"{base}/frames.json"),
                "pose_graph": Link(href=f"{base}/pose_graph.json"),
                "summary": Link(href=f"{base}/summary.json"),
            }
        )
    return links


class MergeRequest(BaseModel):
    """Request body for ``POST /v1/reconstructions:merge``."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    target_recon_id: str
    source_recon_ids: list[str] = Field(..., min_length=1)
    sim3_aligners: list[dict[str, Any]] | None = None
    provider: str | None = Field(
        default=None,
        min_length=1,
        max_length=PROVIDER_SELECTOR_MAX_LENGTH,
        pattern=PROVIDER_SELECTOR_PATTERN,
        description="Optional provider id to execute the merge.",
    )


@router.get("/reconstructions/{recon_id}", response_model=ReconstructionOut)
async def get(
    recon_id: str,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> ReconstructionOut:
    """Read one reconstruction's metadata.

    Returns 404 if the reconstruction doesn't exist for this tenant.
    Use ``links['snapshots']`` / ``links['submodels']`` from the
    response to navigate into the actual outputs.
    """
    r = await reconstruction_service.get_reconstruction(
        session, tenant_id=tenant_id, recon_id=recon_id
    )
    return to_out(ReconstructionOut, r, links=_recon_links(r.recon_id))


@router.get("/reconstructions/{recon_id}/submodels", response_model=Page[SubModelOut])
async def list_submodels(
    recon_id: str,
    page_token: str | None = Query(default=None),
    page_size: int = Query(default=100, ge=1, le=500),
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> Page[SubModelOut]:
    """List the SubModels (disconnected components) of a reconstruction.

    AIP-158 paginated; results within a page are presented in ``idx``
    order (COLMAP component index). Most reconstructions have a
    handful of submodels — pagination matters only for hierarchical
    runs that produce hundreds.
    """
    rows, next_page_token = await reconstruction_service.list_submodels(
        session,
        tenant_id=tenant_id,
        recon_id=recon_id,
        page_size=page_size,
        page_token=page_token,
    )
    items = [to_out(SubModelOut, r, links=_submodel_links(r)) for r in rows]
    return Page[SubModelOut](items=items, next_page_token=next_page_token)


@router.get("/reconstructions/{recon_id}/artifacts", response_model=Page[StageArtifactOut])
async def list_reconstruction_artifacts(
    recon_id: str,
    page_token: str | None = Query(default=None),
    page_size: int = Query(default=100, ge=1, le=500),
    kind: str | None = Query(
        default=None,
        description="Optional exact artifact kind filter, e.g. reconstruction.snapshot.",
    ),
    task_id: str | None = Query(default=None, description="Optional producing task id filter."),
    name: str | None = Query(default=None, description="Optional exact artifact name filter."),
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> Page[StageArtifactOut]:
    """List all typed stage artifacts attached to a reconstruction.

    Use this when a pipeline produces multiple candidate outputs, such
    as dual matchers, alternate verified pair sets, or several mapping
    components.
    """
    await reconstruction_service.get_reconstruction(session, tenant_id=tenant_id, recon_id=recon_id)
    rows, next_page_token = await artifact_service.list_reconstruction_artifacts(
        session,
        tenant_id=tenant_id,
        recon_id=recon_id,
        page_size=page_size,
        page_token=page_token,
        kind=kind,
        task_id=task_id,
        name=name,
    )
    return Page(
        items=[artifact_out(row) for row in rows],
        next_page_token=next_page_token,
    )


@router.get(
    "/reconstructions/{recon_id}/snapshots",
    response_model=SnapshotListResponse,
)
async def list_snapshots(
    recon_id: str,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> SnapshotListResponse:
    """List sealed snapshots for a reconstruction.

    Returns the full sequence of ``seq`` ints + a HAL ``_links`` block
    keyed by ``str(seq)`` plus a ``"latest"`` shortcut. Each link
    block points at the per-snapshot files (``points.bin``,
    ``cameras.json``, etc). Snapshots are immutable once sealed — the
    file routes carry strong ETags + ``immutable`` Cache-Control.
    """
    r = await reconstruction_service.get_reconstruction(
        session, tenant_id=tenant_id, recon_id=recon_id
    )
    paths = Paths()
    seqs = reconstruction_service.list_snapshot_seqs(paths, tenant_id, r.project_id, r.recon_id)

    def _links_for(seq: int) -> dict[str, dict[str, str]]:
        base = f"/v1/reconstructions/{recon_id}/snapshots/{seq}"
        return {
            "self": {"href": base},
            "points": {"href": f"{base}/points.bin"},
            "preview": {"href": f"{base}/points_preview.bin"},
            "cameras": {"href": f"{base}/cameras.json"},
            "images": {"href": f"{base}/images.json"},
            "rigs": {"href": f"{base}/rigs.json"},
            "frames": {"href": f"{base}/frames.json"},
            "pose_graph": {"href": f"{base}/pose_graph.json"},
            "summary": {"href": f"{base}/summary.json"},
            "tiles_index": {"href": f"{base}/tiles/index.json"},
        }

    return SnapshotListResponse(
        seqs=seqs,
        links={
            **{str(s): _links_for(s) for s in seqs},
            "latest": (_links_for(seqs[-1]) if seqs else None),
        },
    )


def _serve_snapshot_file(
    target: Path,
    *,
    name: str,
    request: Request,
    download: bool = False,
) -> Response:
    etag = file_etag(target)
    if if_none_match_hit(request, etag):
        return not_modified(etag)

    media_type = (
        "application/x-sfm-points-v1"
        if name.endswith(".bin")
        else ("application/json" if name.endswith(".json") else "application/octet-stream")
    )
    headers = {
        "ETag": etag,
        "Cache-Control": "public, max-age=31536000, immutable",
    }
    if download:
        headers["Content-Disposition"] = f'attachment; filename="{name}"'
    return FileResponse(target, media_type=media_type, filename=name, headers=headers)


@router.get(
    "/reconstructions/{recon_id}/snapshots/{seq}/{name}",
    response_class=FileResponse,
    responses=_FILE_RESPONSE,
)
async def read_snapshot_file(
    recon_id: str,
    seq: int,
    name: str,
    request: Request,
    download: bool = Query(default=False, description="Force Content-Disposition: attachment"),
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Serve a single sealed-snapshot file. `name` is one of:
    `cameras.json | images.json | rigs.json | frames.json |
    pose_graph.json | points.bin | points_preview.bin | summary.json`.
    Anything else returns 404. Sealed snapshots are immutable, so the
    response carries an `ETag` and honors `If-None-Match`."""
    if "/" in name or ".." in name:
        raise NotFoundError("invalid snapshot file name")
    r = await reconstruction_service.get_reconstruction(
        session, tenant_id=tenant_id, recon_id=recon_id
    )
    paths = Paths()
    snap_dir = reconstruction_service.snapshot_dir(paths, tenant_id, r.project_id, r.recon_id, seq)
    target = snap_dir / name
    if not target.is_file():
        raise NotFoundError(f"snapshot file not found: {name}")
    return _serve_snapshot_file(target, name=name, request=request, download=download)


@router.get(
    "/reconstructions/{recon_id}/snapshots/{seq}/submodels/{idx}/{name}",
    response_class=FileResponse,
    responses=_FILE_RESPONSE,
)
async def read_submodel_snapshot_file(
    recon_id: str,
    seq: int,
    idx: int,
    name: str,
    request: Request,
    download: bool = Query(default=False, description="Force Content-Disposition: attachment"),
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Serve one sealed-snapshot file for a specific disconnected component.

    Multi-model mapping snapshots store component sidecars under
    ``submodels/{idx}`` links backed by ``snapshots/{seq}/{idx}/``. A
    single-model snapshot may only have files at the snapshot root; in
    that case ``idx=0`` falls back to the root files.
    """
    if "/" in name or ".." in name:
        raise NotFoundError("invalid snapshot file name")
    snap_dir = await _resolve_snapshot_dir(session, tenant_id=tenant_id, recon_id=recon_id, seq=seq)
    target = snap_dir / str(idx) / name
    if not target.is_file() and idx == 0:
        fallback = snap_dir / name
        if fallback.is_file():
            target = fallback
    if not target.is_file():
        raise NotFoundError(f"submodel snapshot file not found: {idx}/{name}")
    return _serve_snapshot_file(target, name=name, request=request, download=download)


async def _resolve_snapshot_dir(
    session: AsyncSession, *, tenant_id: str, recon_id: str, seq: int
) -> Path:
    r = await reconstruction_service.get_reconstruction(
        session, tenant_id=tenant_id, recon_id=recon_id
    )
    paths = Paths()
    snap_dir = reconstruction_service.snapshot_dir(paths, tenant_id, r.project_id, r.recon_id, seq)
    if not (snap_dir / ".complete").is_file():
        raise NotFoundError(f"snapshot {seq} not sealed")
    return snap_dir


# ---- octree tiles -------------------------------------------------------


@router.get(
    "/reconstructions/{recon_id}/snapshots/{seq}/tiles/index.json",
    response_class=Response,
    responses=_JSON_FILE_RESPONSE,
)
async def tiles_index(
    recon_id: str,
    seq: int,
    request: Request,
    max_level: int = Query(
        default=tiles_store.DEFAULT_MAX_LEVEL, ge=0, le=tiles_store.MAX_LEVEL_HARD_CAP
    ),
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Octree tile index for the snapshot's `points.bin`. Tiles are
    generated lazily on first request, then cached on disk under
    `<snapshot>/tiles/`. Subsequent requests for tile bytes hit the
    cache directly."""
    snap_dir = await _resolve_snapshot_dir(session, tenant_id=tenant_id, recon_id=recon_id, seq=seq)
    idx_path = tiles_store.ensure_index(snap_dir, max_level=max_level)
    etag = file_etag(idx_path)
    if if_none_match_hit(request, etag):
        return not_modified(etag)
    body = idx_path.read_bytes()
    return Response(
        content=body,
        media_type="application/json",
        headers={
            "ETag": etag,
            "Cache-Control": "public, max-age=31536000, immutable",
        },
    )


@router.get(
    "/reconstructions/{recon_id}/snapshots/{seq}/tiles/{level}/{x}/{y}/{z}.bin",
    response_class=FileResponse,
    responses=_POINT_TILE_RESPONSE,
)
async def read_tile(
    recon_id: str,
    seq: int,
    level: int,
    x: int,
    y: int,
    z: int,
    request: Request,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Serve a single octree tile in `application/x-sfm-points-v1`."""
    if level < 0 or level > tiles_store.MAX_LEVEL_HARD_CAP:
        raise ValidationError(f"level {level} out of range")
    snap_dir = await _resolve_snapshot_dir(session, tenant_id=tenant_id, recon_id=recon_id, seq=seq)
    # Make sure the index (and therefore the tile files) exist.
    tiles_store.ensure_index(snap_dir)
    target = tiles_store.tile_path(snap_dir, tiles_store.TileAddress(level, x, y, z))
    if not target.is_file():
        raise NotFoundError(f"tile {level}/{x}/{y}/{z} not present (empty cell)")
    etag = file_etag(target)
    if if_none_match_hit(request, etag):
        return not_modified(etag)
    return FileResponse(
        target,
        media_type="application/x-sfm-points-v1",
        headers={
            "ETag": etag,
            "Cache-Control": "public, max-age=31536000, immutable",
        },
    )


# ---- observations & visibility -----------------------------------------


@router.get(
    "/reconstructions/{recon_id}/snapshots/{seq}/images/{image_id}/observations",
    response_model=ImageObservationsResponse,
)
async def image_observations(
    recon_id: str,
    seq: int,
    image_id: str,
    request: Request,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Per-image observations: which 3D points the image sees."""
    snap_dir = await _resolve_snapshot_dir(session, tenant_id=tenant_id, recon_id=recon_id, seq=seq)
    if not obs_store.has_observations(snap_dir):
        raise NotFoundError(
            "observations sidecar not present for this snapshot "
            "(server did not emit observations_by_image.json)"
        )
    body = obs_store.read_observations_for_image(snap_dir, image_id)
    if body is None:
        raise NotFoundError(f"no observations for image {image_id}")
    etag = weak_etag("obs_by_image", str(snap_dir), image_id, len(body))
    if if_none_match_hit(request, etag):
        return not_modified(etag)
    payload = ImageObservationsResponse(image_id=image_id, observations=body, count=len(body))
    return JSONResponse(
        payload.model_dump(),
        headers={"ETag": etag, "Cache-Control": "public, max-age=31536000, immutable"},
    )


@router.get(
    "/reconstructions/{recon_id}/snapshots/{seq}/points/{point3d_id}/visibility",
    response_model=PointVisibilityResponse,
)
async def point_visibility(
    recon_id: str,
    seq: int,
    point3d_id: str,
    request: Request,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Per-point visibility: which images observe a given 3D point."""
    snap_dir = await _resolve_snapshot_dir(session, tenant_id=tenant_id, recon_id=recon_id, seq=seq)
    if not obs_store.has_visibility(snap_dir):
        raise NotFoundError(
            "visibility sidecar not present for this snapshot "
            "(server did not emit observations_by_point.json)"
        )
    body = obs_store.read_visibility_for_point(snap_dir, point3d_id)
    if body is None:
        raise NotFoundError(f"no visibility for point3d_id {point3d_id}")
    etag = weak_etag("vis_by_point", str(snap_dir), point3d_id, len(body))
    if if_none_match_hit(request, etag):
        return not_modified(etag)
    payload = PointVisibilityResponse(point3d_id=point3d_id, observations=body, count=len(body))
    return JSONResponse(
        payload.model_dump(),
        headers={"ETag": etag, "Cache-Control": "public, max-age=31536000, immutable"},
    )


@router.post(
    "/reconstructions:merge",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobAcceptedResponse,
)
async def merge_recons_endpoint(
    body: MergeRequest,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Merge several reconstructions into ``target_recon_id``.

    All sources MUST belong to the same project as the target. The
    merged result is sealed as a fresh snapshot under the target's
    workspace; the source reconstructions are left intact."""
    job_id, _tasks, provider = await sfm_stage_service.submit_merge_recons(
        session,
        tenant_id=tenant_id,
        target_recon_id=body.target_recon_id,
        source_recon_ids=body.source_recon_ids,
        sim3_aligners=body.sim3_aligners,
        provider=body.provider,
    )
    return accepted_response(
        JobAcceptedResponse(
            job_id=job_id,
            recon_id=body.target_recon_id,
            target_recon_id=body.target_recon_id,
            source_recon_ids=body.source_recon_ids,
            provider=provider,
        )
    )


@router.get(
    "/reconstructions/{recon_id}/correspondence_graph.json",
    response_class=FileResponse,
    responses=_JSON_FILE_RESPONSE,
)
async def read_correspondence_graph(
    recon_id: str,
    request: Request,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Serve the **raw** (pre-verification) correspondence graph.

    Lives at the reconstruction level — emitted by the match worker
    after every match run. Use ``two_view_geometries.json`` to see the
    verified inlier subset; use this file to debug "why didn't this
    pair survive verification?"
    """
    r = await reconstruction_service.get_reconstruction(
        session, tenant_id=tenant_id, recon_id=recon_id
    )
    paths = Paths()
    target = (
        paths.reconstruction_root(tenant_id, r.project_id, r.recon_id) / "correspondence_graph.json"
    )
    if not target.is_file():
        raise NotFoundError(
            "correspondence_graph.json not present "
            "(match has not run, or the worker failed to export it)"
        )
    etag = file_etag(target)
    if if_none_match_hit(request, etag):
        return not_modified(etag)
    return FileResponse(
        target,
        media_type="application/json",
        headers={"ETag": etag, "Cache-Control": "public, max-age=60"},
    )


@router.get(
    "/reconstructions/{recon_id}/two_view_geometries.json",
    response_class=FileResponse,
    responses=_JSON_FILE_RESPONSE,
)
async def read_two_view_geometries(
    recon_id: str,
    request: Request,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Serve the verified two-view geometries for this reconstruction.

    Lives at the **reconstruction** level (not per snapshot) because the
    file tracks the database state — every verify run can update it. The
    file is emitted by the verify worker into the reconstruction root.
    """
    r = await reconstruction_service.get_reconstruction(
        session, tenant_id=tenant_id, recon_id=recon_id
    )
    paths = Paths()
    target = (
        paths.reconstruction_root(tenant_id, r.project_id, r.recon_id) / "two_view_geometries.json"
    )
    if not target.is_file():
        raise NotFoundError(
            "two_view_geometries.json not present "
            "(verify has not run, or the worker failed to export pairs)"
        )
    etag = file_etag(target)
    if if_none_match_hit(request, etag):
        return not_modified(etag)
    return FileResponse(
        target,
        media_type="application/json",
        headers={"ETag": etag, "Cache-Control": "public, max-age=60"},
    )


@router.get("/submodels/{submodel_id}", response_model=SubModelOut)
async def get_submodel(
    submodel_id: str,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> SubModelOut:
    """Read one SubModel by its canonical ``submodel_id``.

    Direct read of a single connected component without going through
    ``GET /v1/reconstructions/{recon_id}/submodels``. 404 if absent.
    """
    sm = await reconstruction_service.get_submodel(
        session, tenant_id=tenant_id, submodel_id=submodel_id
    )
    return to_out(SubModelOut, sm, links=_submodel_links(sm))
