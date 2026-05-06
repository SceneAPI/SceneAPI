"""Project routes — CRUD + dataset ingest helpers (video / Kapture)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1._helpers import accepted_response
from app.core.tenancy import current_tenant
from app.db.session import get_db
from app.schemas.api.common import Link, to_out
from app.schemas.api.jobs import JobAcceptedResponse
from app.schemas.api.projects import (
    ProjectCreate,
    ProjectListPage,
    ProjectOut,
    ProjectPatch,
)
from app.services import project_service, sfm_stage_service

router = APIRouter(prefix="/projects", tags=["projects"])


def _project_links(project_id: str) -> dict[str, Link]:
    return {
        "self": Link(href=f"/v1/projects/{project_id}"),
        "datasets": Link(href=f"/v1/projects/{project_id}/datasets"),
        "pipelines": Link(href=f"/v1/projects/{project_id}/pipelines"),
    }


def _to_out(p) -> ProjectOut:
    return to_out(ProjectOut, p, links=_project_links(p.project_id))


class VideoFramesRequest(BaseModel):
    """``POST /v1/projects/{pid}/datasets:from_video`` — extract
    keyframes from a worker-local video file."""

    model_config = ConfigDict(populate_by_name=True)

    video_path: str
    fps: float = Field(default=2.0, gt=0, le=120.0)
    max_frames: int = Field(default=1000, ge=1, le=100000)


class KaptureImportRequest(BaseModel):
    """``POST /v1/projects/{pid}/datasets:import_kapture``."""

    model_config = ConfigDict(populate_by_name=True)

    archive_path: str  # extracted Kapture archive root on the worker


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
    return ProjectListPage(
        items=[_to_out(r) for r in rows], next_page_token=next_page_token
    )


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
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> ProjectOut:
    """Partially update a project.

    Only the fields present in the request body are written; unset
    fields are left untouched (Pydantic ``exclude_unset=True``).
    Returns the post-update :class:`ProjectOut` body.
    """
    p = await project_service.patch_project(
        session,
        tenant_id=tenant_id,
        project_id=project_id,
        updates=body.model_dump(exclude_unset=True),
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
    "/{project_id}/datasets:from_video",
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
    return accepted_response(
        JobAcceptedResponse(job_id=job_id, project_id=project_id)
    )


@router.post(
    "/{project_id}/datasets:import_kapture",
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
    return accepted_response(
        JobAcceptedResponse(job_id=job_id, project_id=project_id)
    )
