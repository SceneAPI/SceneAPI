"""Dataset routes — CRUD + PATCH + spherical render."""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from sfmapi.server.api.v1._helpers import accepted_response, masked_updates
from sfmapi.server.core.tenancy import current_tenant
from sfmapi.server.db.models import Dataset
from sfmapi.server.db.session import get_db
from sfmapi.server.schemas.api.common import Link, Page, to_out
from sfmapi.server.schemas.api.datasets import (
    DatasetCreate,
    DatasetOut,
    DatasetPatch,
)
from sfmapi.server.schemas.api.jobs import JobAcceptedResponse
from sfmapi.server.schemas.api.projections import (
    CubemapProjectionRequest,
    EquirectangularProjectionRequest,
    PerspectiveProjectionRequest,
    ProjectionJobRequest,
)
from sfmapi.server.services import dataset_service, project_service, sfm_stage_service

router = APIRouter(prefix="/projects/{project_id}/datasets", tags=["datasets"])


def _dataset_links(project_id: str, dataset_id: str) -> dict[str, Link]:
    return {
        "self": Link(href=f"/v1/projects/{project_id}/datasets/{dataset_id}"),
        "project": Link(href=f"/v1/projects/{project_id}"),
        "images": Link(href=f"/v1/datasets/{dataset_id}/images"),
        "features": Link(href=f"/v1/datasets/{dataset_id}/features"),
        "matches": Link(href=f"/v1/datasets/{dataset_id}/matches"),
        "verify": Link(href=f"/v1/datasets/{dataset_id}/verify"),
        "similarity": Link(href=f"/v1/datasets/{dataset_id}/similarity"),
    }


def _to_out(d: Dataset) -> DatasetOut:
    return to_out(DatasetOut, d, links=_dataset_links(d.project_id, d.dataset_id))


@router.post("", response_model=DatasetOut, status_code=status.HTTP_201_CREATED)
async def create(
    project_id: str,
    body: DatasetCreate,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> DatasetOut:
    """Create a Dataset under a project.

    ``body.source`` is a discriminated :data:`SourceSpec` (``upload``
    | ``local`` | ``s3``); the source is materialized server-side and
    bound to the new Dataset. ``camera_model`` / ``intrinsics_mode`` /
    ``is_spherical`` / ``rig_config`` configure the SfM pipeline
    defaults. 404 if the project doesn't exist for this tenant.
    """
    await project_service.get_project(session, tenant_id=tenant_id, project_id=project_id)
    src = await dataset_service.create_image_source(
        session, tenant_id=tenant_id, source=body.source
    )

    d = await dataset_service.create_dataset(
        session,
        tenant_id=tenant_id,
        project_id=project_id,
        source_id=src.source_id,
        name=body.name,
        camera_model=body.camera_model,
        intrinsics_mode=body.intrinsics_mode,
        is_spherical=body.is_spherical,
        rig_config=body.rig_config,
        respect_exif_orientation=body.respect_exif_orientation,
    )
    return _to_out(d)


@router.get("", response_model=Page[DatasetOut])
async def list_(
    project_id: str,
    page_token: str | None = Query(default=None),
    page_size: int = Query(default=100, ge=1, le=500),
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> Page[DatasetOut]:
    """List datasets under a project (AIP-158 paginated).

    Iterate by threading ``next_page_token`` back; ``null`` ends the
    cursor. ``total`` is omitted (returned as ``null``) — counting
    the full collection is a separate query and is not always cheap.
    """
    rows, next_page_token = await dataset_service.list_datasets(
        session,
        tenant_id=tenant_id,
        project_id=project_id,
        page_size=page_size,
        page_token=page_token,
    )
    return Page[DatasetOut](items=[_to_out(r) for r in rows], next_page_token=next_page_token)


@router.get("/{dataset_id}", response_model=DatasetOut)
async def get(
    project_id: str,
    dataset_id: str,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> DatasetOut:
    """Read a single dataset by id.

    422 ``ValidationError`` if the dataset belongs to a different
    project than the one in the path; 404 if it doesn't exist for
    this tenant at all.
    """
    d = await dataset_service.get_dataset(
        session, tenant_id=tenant_id, dataset_id=dataset_id, project_id=project_id
    )
    return _to_out(d)


@router.delete("/{dataset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete(
    project_id: str,
    dataset_id: str,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> None:
    """Delete a dataset.

    Cascades through registered images, derived feature/match files,
    similarity indexes, and dependent reconstructions. 422 if the
    dataset belongs to a different project; 404 if it doesn't exist.
    """
    await dataset_service.get_dataset(
        session, tenant_id=tenant_id, dataset_id=dataset_id, project_id=project_id
    )
    await dataset_service.delete_dataset(session, tenant_id=tenant_id, dataset_id=dataset_id)


@router.patch("/{dataset_id}", response_model=DatasetOut)
async def patch(
    project_id: str,
    dataset_id: str,
    body: DatasetPatch,
    update_mask: str | None = Query(
        default=None,
        description=(
            "Optional AIP-161 comma-separated field mask. Allowed paths: "
            "name, camera_model, intrinsics_mode, is_spherical, rig_config, "
            "respect_exif_orientation, active_maskset_id."
        ),
    ),
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> DatasetOut:
    """Partially update a dataset.

    Without ``update_mask``, only fields present in the request body
    are written. With ``update_mask``, only the named field paths are
    applied and they must also be present in the body.

    The dataset's ``source_id`` is immutable; to change image inputs,
    create a new dataset. 422 if the row exists but belongs to another project.
    """
    await dataset_service.get_dataset(
        session, tenant_id=tenant_id, dataset_id=dataset_id, project_id=project_id
    )
    d = await dataset_service.patch_dataset(
        session,
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        updates=masked_updates(
            body,
            update_mask,
            allowed={
                "name",
                "camera_model",
                "intrinsics_mode",
                "is_spherical",
                "rig_config",
                "respect_exif_orientation",
                "active_maskset_id",
            },
        ),
    )
    return _to_out(d)


# ---- spherical → cubemap (image-level) ---------------------------------

# Mounted at the top-level prefix because the action target is
# `/v1/datasets/{did}:renderCubemap` (no project segment) — siblings
# of the existing `/v1/datasets/{did}/...` reads in images.py.
spherical_router = APIRouter(prefix="/datasets/{dataset_id}", tags=["datasets"])


@spherical_router.post(
    ":renderCubemap",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobAcceptedResponse,
)
async def render_cubemap(
    dataset_id: str,
    body: CubemapProjectionRequest | None = Body(default=None),
    face_size: int | None = Query(
        default=None, ge=64, le=8192, description="Pixel edge length per cubemap face"
    ),
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Render every spherical panorama in this dataset into 6 cubemap faces.

    Requires the dataset to be marked ``is_spherical=true``. The
    output directory is returned in the task result; clients can then
    register it as a new ``local`` source for downstream pinhole-only
    pipelines.
    """
    if body is None:
        body = CubemapProjectionRequest()
    job_id, _tasks, resolved_provider = await sfm_stage_service.submit_render_cubemap(
        session,
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        face_size=face_size,
        spec=body.operation_spec(),
        provider=body.provider,
    )
    return accepted_response(
        JobAcceptedResponse(job_id=job_id, dataset_id=dataset_id, provider=resolved_provider)
    )


@spherical_router.post(
    ":projectImages",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobAcceptedResponse,
)
async def project_images(
    dataset_id: str,
    body: ProjectionJobRequest,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Run a portable projection transform over the dataset images."""
    job_id, _tasks, resolved_provider = await sfm_stage_service.submit_project_images(
        session,
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        spec=body.model_dump(mode="json"),
    )
    return accepted_response(
        JobAcceptedResponse(job_id=job_id, dataset_id=dataset_id, provider=resolved_provider)
    )


@spherical_router.post(
    ":renderEquirectangular",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobAcceptedResponse,
)
async def render_equirectangular(
    dataset_id: str,
    body: EquirectangularProjectionRequest | None = Body(default=None),
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Render a cubemap dataset back to equirectangular panoramas."""
    if body is None:
        body = EquirectangularProjectionRequest()
    job_id, _tasks, resolved_provider = await sfm_stage_service.submit_project_images(
        session,
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        spec=body.model_dump(mode="json"),
        recipe="render_equirectangular",
    )
    return accepted_response(
        JobAcceptedResponse(job_id=job_id, dataset_id=dataset_id, provider=resolved_provider)
    )


@spherical_router.post(
    ":renderPerspective",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobAcceptedResponse,
)
async def render_perspective(
    dataset_id: str,
    body: PerspectiveProjectionRequest | None = Body(default=None),
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Render pinhole perspective views from spherical panoramas."""
    if body is None:
        body = PerspectiveProjectionRequest()
    job_id, _tasks, resolved_provider = await sfm_stage_service.submit_project_images(
        session,
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        spec=body.model_dump(mode="json"),
        recipe="render_perspective",
    )
    return accepted_response(
        JobAcceptedResponse(job_id=job_id, dataset_id=dataset_id, provider=resolved_provider)
    )
