"""Project routes — CRUD + dataset ingest helpers (video / Kapture)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from sceneapi.server.api.v1._helpers import accepted_response, masked_updates
from sceneapi.server.core.tenancy import current_tenant
from sceneapi.server.db.models import Project
from sceneapi.server.db.session import get_db
from sceneapi.server.schemas.api.common import Link, to_out
from sceneapi.server.schemas.api.jobs import JobAcceptedResponse
from sceneapi.server.schemas.api.projects import (
    ProjectCreate,
    ProjectListPage,
    ProjectOut,
    ProjectPatch,
)
from sceneapi.server.services import project_service, sfm_stage_service

router = APIRouter(prefix="/projects", tags=["projects"])


def _project_links(project_id: str) -> dict[str, Link]:
    return {
        "self": Link(href=f"/v1/projects/{project_id}"),
        "datasets": Link(href=f"/v1/projects/{project_id}/datasets"),
        "pipelines": Link(href=f"/v1/projects/{project_id}/pipelines"),
    }


def _to_out(p: Project) -> ProjectOut:
    return to_out(ProjectOut, p, links=_project_links(p.project_id))


class VideoFramesRequest(BaseModel):
    """``POST /v1/projects/{pid}/datasets:fromVideo`` — extract
    keyframes from a worker-local video file."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    video_path: str
    fps: float = Field(default=2.0, gt=0, le=120.0)
    max_frames: int = Field(default=1000, ge=1, le=100000)


class KaptureImportRequest(BaseModel):
    """``POST /v1/projects/{pid}/datasets:importKapture``."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    archive_path: str  # extracted Kapture archive root on the worker


class ArchiveImportRequest(BaseModel):
    """``POST /v1/projects/{pid}/datasets:fromArchive`` — register a
    dataset from an already-uploaded image zip.

    Upload the zip through the normal chunked-upload protocol first
    (``POST /v1/uploads`` → ``PATCH`` → ``:finalize`` → ``blob_sha``);
    this route only enqueues the unpack. The worker decodes the archive
    straight from the blob store (in memory for the ephemeral backend),
    extracts the image entries, and registers a derived dataset — one
    call instead of N per-image registrations."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    blob_sha: str = Field(
        ...,
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
        description="Content address of the finalized zip upload.",
    )
    name: str | None = Field(default=None, max_length=255)
    camera_model: str = "SIMPLE_RADIAL"
    intrinsics_mode: str = "single_camera"
    is_spherical: bool = False
    image_prefix: str | None = Field(
        default=None,
        max_length=1024,
        description=(
            "Restrict the import to entries under this zip subpath "
            "(e.g. 'south-building/images/'). When unset the worker "
            "auto-detects the common image directory."
        ),
    )


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create(
    body: ProjectCreate,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> ProjectOut:
    """Create a new Project under the caller's tenant.

    Projects are the top-level workspace; every Dataset / Reconstruction
    rolls up under one. ``name`` is a human label and is NOT unique —
    rely on the returned ``project_id`` (a ULID) as the canonical key.
    """
    p = await project_service.create_project(
        session, tenant_id=tenant_id, name=body.name, description=body.description
    )
    return _to_out(p)


@router.get("", response_model=ProjectListPage)
async def list_(
    page_token: str | None = Query(None),
    page_size: int = Query(50, ge=1, le=500),
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> ProjectListPage:
    """List the caller's projects, AIP-158 paginated.

    Pass an empty ``page_token`` for the first page; iterate by
    threading the previous response's ``next_page_token`` back. A
    ``null`` ``next_page_token`` means the iteration is exhausted.
    """
    rows, next_page_token = await project_service.list_projects(
        session, tenant_id=tenant_id, page_size=page_size, page_token=page_token
    )
    return ProjectListPage(items=[_to_out(r) for r in rows], next_page_token=next_page_token)


@router.get("/{project_id}", response_model=ProjectOut)
async def get(
    project_id: str,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> ProjectOut:
    """Read a single project by id.

    Returns 404 when the project doesn't exist for this tenant — a row
    that exists under another tenant looks identical to "not present"
    by design (see ``L2`` tenant boundary).
    """
    p = await project_service.get_project(session, tenant_id=tenant_id, project_id=project_id)
    return _to_out(p)


@router.patch("/{project_id}", response_model=ProjectOut)
async def patch(
    project_id: str,
    body: ProjectPatch,
    update_mask: str | None = Query(
        default=None,
        description=(
            "Optional AIP-161 comma-separated field mask. Allowed paths: name, description."
        ),
    ),
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> ProjectOut:
    """Partially update a project.

    Without ``update_mask``, only fields present in the request body
    are written. With ``update_mask``, only the named field paths are
    applied and they must also be present in the body.
    """
    p = await project_service.patch_project(
        session,
        tenant_id=tenant_id,
        project_id=project_id,
        updates=masked_updates(body, update_mask, allowed={"name", "description"}),
    )
    return _to_out(p)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete(
    project_id: str,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> None:
    """Delete a project (cascade).

    Cascades through datasets, reconstructions, jobs, and on-disk
    workspace state. Returns 204 on success, 404 when the project
    doesn't exist for this tenant. Conflict-409 if active jobs prevent
    a clean teardown — cancel them first.
    """
    await project_service.delete_project(session, tenant_id=tenant_id, project_id=project_id)


@router.post(
    "/{project_id}/datasets:fromVideo",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobAcceptedResponse,
)
async def from_video(
    project_id: str,
    body: VideoFramesRequest,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Extract frames from a worker-local video file. The result carries
    the output directory; the client follows up with a ``local``-source
    dataset pointing at it."""
    job_id, _tasks = await sfm_stage_service.submit_video_frames(
        session,
        tenant_id=tenant_id,
        project_id=project_id,
        video_path=body.video_path,
        fps=body.fps,
        max_frames=body.max_frames,
    )
    return accepted_response(JobAcceptedResponse(job_id=job_id, project_id=project_id))


@router.post(
    "/{project_id}/datasets:importKapture",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobAcceptedResponse,
)
async def import_kapture(
    project_id: str,
    body: KaptureImportRequest,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Parse a Kapture archive into a sensors+records inventory; the
    job result carries the parsed contents and the recommended
    ``image_root`` so the client can register a ``local`` dataset
    pointing at it."""
    job_id, _tasks = await sfm_stage_service.submit_kapture_import(
        session,
        tenant_id=tenant_id,
        project_id=project_id,
        archive_path=body.archive_path,
    )
    return accepted_response(JobAcceptedResponse(job_id=job_id, project_id=project_id))


@router.post(
    "/{project_id}/datasets:fromArchive",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobAcceptedResponse,
)
async def from_archive(
    project_id: str,
    body: ArchiveImportRequest,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Register a dataset from an already-uploaded image zip.

    Collapses the N-per-image registration flow to one call: the worker
    decodes the finalized zip (``blob_sha``) straight from the blob
    store, enforces the uncompressed-size cap, extracts the images, and
    the dispatcher registers the resulting derived dataset. Follow
    ``Location`` to the job; the terminal job's task carries
    ``num_images`` and the registered ``derived_dataset``."""
    job_id, _tasks = await sfm_stage_service.submit_dataset_from_archive(
        session,
        tenant_id=tenant_id,
        project_id=project_id,
        blob_sha=body.blob_sha,
        name=body.name,
        camera_model=body.camera_model,
        intrinsics_mode=body.intrinsics_mode,
        is_spherical=body.is_spherical,
        image_prefix=body.image_prefix,
    )
    return accepted_response(JobAcceptedResponse(job_id=job_id, project_id=project_id))
