"""Image routes — register, read bytes, thumbnail, EXIF."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import FileResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import NotFoundError, ValidationError
from app.core.http import file_etag, if_none_match_hit, not_modified, weak_etag
from app.core.tenancy import current_tenant
from app.db.models import Image
from app.db.session import get_db
from app.schemas.api.common import Link, Page, to_out
from app.schemas.api.images import (
    BatchCreateImagesRequest,
    BatchCreateImagesResponse,
    ImageCreate,
    ImageExifResponse,
    ImageOut,
    PosePriorsBulkResponse,
    PosePriorsBulkWriteResponse,
)
from app.schemas.api.scene import PosePrior
from app.services import dataset_service, image_bytes_service, image_service
from app.storage.thumbs import get_or_create as get_or_create_thumb

# Three routers under one module:
#   - dataset-nested CRUD ("/datasets/{did}/images")
#   - top-level reads ("/images/{image_id}")
#   - dataset-level pose-prior bulk ("/datasets/{did}/pose_priors")

router = APIRouter(prefix="/datasets/{dataset_id}/images", tags=["images"])
read_router = APIRouter(prefix="/images", tags=["images"])
dataset_router = APIRouter(prefix="/datasets/{dataset_id}", tags=["images"])

_BINARY_SCHEMA = {"schema": {"type": "string", "format": "binary"}}
_IMAGE_BYTE_MEDIA_TYPES = (
    "application/octet-stream",
    "image/bmp",
    "image/heic",
    "image/heif",
    "image/jpeg",
    "image/png",
    "image/tiff",
    "image/webp",
)
_BINARY_RESPONSE = {
    200: {
        "content": {media_type: _BINARY_SCHEMA for media_type in _IMAGE_BYTE_MEDIA_TYPES},
        "description": "Binary image bytes.",
    }
}

_JPEG_RESPONSE = {
    200: {
        "content": {
            "application/octet-stream": {
                "schema": {"type": "string", "format": "binary"}
            },
            "image/jpeg": {"schema": {"type": "string", "format": "binary"}}
        },
        "description": "JPEG image bytes.",
    }
}


def _image_links(img: Image) -> dict[str, Link]:
    return {
        "self": Link(href=f"/v1/images/{img.image_id}"),
        "bytes": Link(href=f"/v1/images/{img.image_id}/bytes"),
        "thumbnail": Link(href=f"/v1/images/{img.image_id}/thumbnail"),
        "exif": Link(href=f"/v1/images/{img.image_id}/exif"),
        "dataset": Link(href=f"/v1/datasets/{img.dataset_id}"),
    }


def _to_out(img: Image) -> ImageOut:
    return to_out(ImageOut, img, links=_image_links(img))


def _resolve_kind(body: ImageCreate) -> tuple[str, str]:
    has_blob = body.blob_sha is not None
    has_rel_path = body.rel_path is not None
    if has_blob and has_rel_path:
        raise ValidationError("Exactly one of blob_sha or rel_path is required")
    if has_blob:
        return "upload", body.blob_sha
    if has_rel_path:
        return "local", "0" * 64  # placeholder; computed on first read
    raise ValidationError("Either blob_sha or rel_path is required")


@router.post("", response_model=ImageOut, status_code=status.HTTP_201_CREATED)
async def create(
    dataset_id: str,
    body: ImageCreate,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> ImageOut:
    """Register a single image in a dataset.

    Provide ``blob_sha`` for upload-source datasets (the value is the
    canonical sha returned by ``POST /v1/uploads/{id}:finalize``) or
    ``rel_path`` for local-source datasets (the path relative to the
    source root). Exactly one MUST be set; 422 ``ValidationError``
    otherwise. For batch ingestion use ``POST :batchCreate``.
    """
    d = await dataset_service.get_dataset(session, tenant_id=tenant_id, dataset_id=dataset_id)
    kind, sha = _resolve_kind(body)
    img = await image_service.add_image(
        session,
        tenant_id=tenant_id,
        dataset=d,
        name=body.name,
        content_sha=sha,
        source_kind=kind,
        rel_path=body.rel_path,
        width=body.width,
        height=body.height,
        exif=body.exif,
    )
    return _to_out(img)


@router.post(
    ":batchCreate",
    response_model=BatchCreateImagesResponse,
    status_code=status.HTTP_201_CREATED,
)
async def batch_create(
    dataset_id: str,
    body: BatchCreateImagesRequest,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> BatchCreateImagesResponse:
    """Bulk-register images in a single transaction (AIP-231).

    Up to 1000 ``requests`` per call. Each entry is a complete
    ``ImageCreate``; failures abort the whole batch (atomic).
    """
    if not body.requests:
        raise ValidationError("requests must be non-empty")
    if len(body.requests) > 1000:
        raise ValidationError("batch limit is 1000 items")
    d = await dataset_service.get_dataset(session, tenant_id=tenant_id, dataset_id=dataset_id)
    out: list[Image] = []
    for item in body.requests:
        kind, sha = _resolve_kind(item)
        img = await image_service.add_image(
            session,
            tenant_id=tenant_id,
            dataset=d,
            name=item.name,
            content_sha=sha,
            source_kind=kind,
            rel_path=item.rel_path,
            width=item.width,
            height=item.height,
            exif=item.exif,
        )
        out.append(img)
    return BatchCreateImagesResponse(images=[_to_out(i) for i in out])


@router.get("", response_model=Page[ImageOut])
async def list_(
    dataset_id: str,
    page_token: str | None = Query(None),
    page_size: int = Query(100, ge=1, le=500),
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> Page[ImageOut]:
    """List images in a dataset (AIP-158 paginated).

    Ordered by ``created_at`` ascending — registration order. Iterate
    by threading ``next_page_token`` back; ``null`` ends the cursor.
    """
    rows, next_page_token = await image_service.list_images(
        session,
        tenant_id=tenant_id,
        dataset_id=dataset_id,
        page_size=page_size,
        page_token=page_token,
    )
    return Page[ImageOut](items=[_to_out(r) for r in rows], next_page_token=next_page_token)


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete(
    dataset_id: str,
    name: str,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> None:
    """Unregister an image from a dataset by ``name``.

    NOTE: addressed by the human-readable ``name`` here, not the
    canonical ``image_id`` (the audit doc captures the ergonomic
    inconsistency — kept stable in place; reads + bytes routes
    use ``image_id``). 204 on success, 404 if no image with that
    name exists in the dataset.
    """
    await image_service.delete_image(session, tenant_id=tenant_id, dataset_id=dataset_id, name=name)


# ---- top-level reads -------------------------------------------------------


@read_router.get("/{image_id}", response_model=ImageOut)
async def get_image(
    image_id: str,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> ImageOut:
    """Read one image's metadata by its canonical ``image_id``.

    Returns the same shape as :class:`ImageOut` — width / height /
    EXIF / source pointers — without the bytes themselves. Use
    ``GET /v1/images/{id}/bytes`` for the original payload.
    """
    img = await image_service.get_image(session, tenant_id=tenant_id, image_id=image_id)
    return _to_out(img)


@read_router.delete("/{image_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_image_by_id(
    image_id: str,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> None:
    """Unregister an image by canonical ``image_id``.

    This is the AIP-122-aligned delete path. The legacy
    ``DELETE /v1/datasets/{dataset_id}/images/{name}`` route remains
    for compatibility with clients that address images by label.
    """
    await image_service.delete_image_by_id(session, tenant_id=tenant_id, image_id=image_id)


@read_router.get(
    "/{image_id}/bytes",
    response_class=FileResponse,
    responses=_BINARY_RESPONSE,
)
async def get_image_bytes(
    image_id: str,
    request: Request,
    download: bool = Query(default=False),
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Stream the original image bytes. Carries an ETag for HTTP caches."""
    img = await image_service.get_image(session, tenant_id=tenant_id, image_id=image_id)
    path = await image_bytes_service.resolve_image_path(session, tenant_id=tenant_id, image=img)
    if not path.is_file():
        raise NotFoundError(f"image bytes missing on disk for {image_id}")
    etag = file_etag(path)
    if if_none_match_hit(request, etag):
        return not_modified(etag)
    headers = {"ETag": etag, "Cache-Control": "private, max-age=3600"}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="{img.name}"'
    # Blob paths have no extension; derive content type from the
    # image name instead.
    suffix = ("." + img.name.rsplit(".", 1)[-1].lower()) if "." in img.name else ""
    media_type = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
        ".bmp": "image/bmp",
        ".webp": "image/webp",
        ".heic": "image/heic",
        ".heif": "image/heif",
    }.get(suffix, "application/octet-stream")
    return FileResponse(path, media_type=media_type, headers=headers)


@read_router.get(
    "/{image_id}/thumbnail",
    response_class=FileResponse,
    responses=_JPEG_RESPONSE,
)
async def get_image_thumbnail(
    image_id: str,
    request: Request,
    size: int | None = Query(default=None, ge=16, description="Max edge length in pixels."),
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """JPEG thumbnail, max edge `size` (default 256, hard-cap from settings)."""
    s = get_settings()
    requested = size or s.thumbnail_default_size
    if requested > s.thumbnail_max_size:
        raise ValidationError(f"thumbnail size exceeds max {s.thumbnail_max_size}")
    img = await image_service.get_image(session, tenant_id=tenant_id, image_id=image_id)
    src_path = await image_bytes_service.resolve_image_path(session, tenant_id=tenant_id, image=img)
    if not src_path.is_file():
        raise NotFoundError("source image missing on disk")
    sha = (
        img.content_sha
        if img.content_sha and img.content_sha != "0" * 64
        else file_etag(src_path).strip('"').replace(":", "_")
    )
    out = get_or_create_thumb(src_path, sha, requested)
    etag = weak_etag("thumb", sha, requested)
    if if_none_match_hit(request, etag):
        return not_modified(etag)
    return FileResponse(
        out,
        media_type="image/jpeg",
        headers={"ETag": etag, "Cache-Control": "public, max-age=86400"},
    )


@read_router.get("/{image_id}/exif", response_model=ImageExifResponse)
async def get_image_exif(
    image_id: str,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> ImageExifResponse:
    """Return the image's EXIF metadata as a free-form dict.

    Uses the cached ``exif_json`` row when present; otherwise falls
    back to extracting from the on-disk bytes. Returns an empty
    ``exif`` map (not 404) when the source has no EXIF or the bytes
    can't be located.
    """
    img = await image_service.get_image(session, tenant_id=tenant_id, image_id=image_id)
    if img.exif_json:
        return ImageExifResponse(exif=img.exif_json)
    try:
        path = await image_bytes_service.resolve_image_path(session, tenant_id=tenant_id, image=img)
    except NotFoundError:
        return ImageExifResponse(exif={})
    if path.is_file():
        return ImageExifResponse(exif=image_bytes_service.extract_exif(path))
    return ImageExifResponse(exif={})


# ---- pose priors ---------------------------------------------------------


@read_router.get("/{image_id}/pose_prior", response_model=PosePrior | None)
async def get_pose_prior(
    image_id: str,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> PosePrior | None:
    """Return the image's PosePrior (or `null` if none is set)."""
    img = await image_service.get_image(session, tenant_id=tenant_id, image_id=image_id)
    if img.pose_prior_json is None:
        return None
    return PosePrior.model_validate(img.pose_prior_json)


@read_router.put("/{image_id}/pose_prior", response_model=PosePrior)
async def put_pose_prior(
    image_id: str,
    body: PosePrior,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> PosePrior:
    """Set (or replace) the PosePrior on an image."""
    payload = body.model_dump(mode="json", by_alias=True)
    await image_service.set_pose_prior(
        session, tenant_id=tenant_id, image_id=image_id, prior=payload
    )
    return body


@read_router.delete("/{image_id}/pose_prior", status_code=status.HTTP_204_NO_CONTENT)
async def delete_pose_prior(
    image_id: str,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> None:
    """Clear the PosePrior on an image."""
    await image_service.set_pose_prior(session, tenant_id=tenant_id, image_id=image_id, prior=None)


@dataset_router.get("/pose_priors", response_model=PosePriorsBulkResponse)
async def list_dataset_pose_priors(
    dataset_id: str,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> PosePriorsBulkResponse:
    """All PosePriors for the dataset, keyed by image_id."""
    rows = await image_service.list_pose_priors(session, tenant_id=tenant_id, dataset_id=dataset_id)
    return PosePriorsBulkResponse(pose_priors={img.image_id: prior for img, prior in rows})


@dataset_router.put("/pose_priors", response_model=PosePriorsBulkWriteResponse)
async def bulk_set_pose_priors(
    dataset_id: str,
    body: dict[str, PosePrior],
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> PosePriorsBulkWriteResponse:
    """Bulk-set PosePriors for the dataset. Body is `{image_id: PosePrior}`.
    Existing priors for image_ids not in the body are left untouched —
    use `DELETE /v1/images/{image_id}/pose_prior` to clear individuals."""
    written = 0
    for image_id, prior in body.items():
        await image_service.set_pose_prior(
            session,
            tenant_id=tenant_id,
            image_id=image_id,
            prior=prior.model_dump(mode="json", by_alias=True),
        )
        written += 1
    return PosePriorsBulkWriteResponse(written=written)
