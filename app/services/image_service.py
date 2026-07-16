"""Image CRUD inside a dataset."""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.path_safety import validate_safe_relative_path
from app.db.models import Blob, Dataset, Image, ImageSource
from app.db.pagination import paginate_keyset
from app.services.dataset_service import recompute_manifest_hash


async def add_image(
    session: AsyncSession,
    *,
    tenant_id: str,
    dataset: Dataset,
    name: str,
    content_sha: str,
    source_kind: str,
    rel_path: str | None = None,
    byte_size: int | None = None,
    width: int | None = None,
    height: int | None = None,
    exif: dict[str, Any] | None = None,
) -> Image:
    validate_safe_relative_path(name, field="name")
    source = await session.get(ImageSource, dataset.source_id)
    if source is None:
        raise NotFoundError("dataset source not found")
    if source.kind != source_kind:
        raise ValidationError(f"{source_kind} images require a {source_kind} dataset source")
    if source_kind == "upload":
        result = await session.execute(select(Blob).where(Blob.sha256 == content_sha))
        b = result.scalar_one_or_none()
        if b is None:
            raise NotFoundError(f"Blob {content_sha} not found")
        b.refcount = b.refcount + 1
        if byte_size is None:
            byte_size = b.byte_size
    elif source_kind == "local":
        if rel_path is None:
            raise ValidationError("local images require rel_path")
        rel_path = validate_safe_relative_path(rel_path, field="rel_path")
    img = Image(
        tenant_id=tenant_id,
        dataset_id=dataset.dataset_id,
        name=name,
        content_sha=content_sha,
        source_kind=source_kind,
        rel_path=rel_path,
        byte_size=byte_size,
        width=width,
        height=height,
        exif_json=exif,
    )
    session.add(img)
    try:
        await session.flush()
    except IntegrityError as e:
        await session.rollback()
        raise ConflictError(f"Image {name!r} already exists in dataset") from e
    await recompute_manifest_hash(session, dataset_id=dataset.dataset_id)
    return img


async def list_images(
    session: AsyncSession,
    *,
    tenant_id: str,
    dataset_id: str,
    page_size: int = 100,
    page_token: str | None = None,
) -> tuple[list[Image], str | None]:
    stmt = select(Image).where(Image.tenant_id == tenant_id, Image.dataset_id == dataset_id)
    return await paginate_keyset(
        session,
        stmt,
        pk=Image.image_id,
        page_size=page_size,
        page_token=page_token,
    )


async def get_image(session: AsyncSession, *, tenant_id: str, image_id: str) -> Image:
    result = await session.execute(
        select(Image).where(Image.tenant_id == tenant_id, Image.image_id == image_id)
    )
    img = result.scalar_one_or_none()
    if img is None:
        raise NotFoundError(f"Image {image_id} not found")
    return img


async def set_pose_prior(
    session: AsyncSession, *, tenant_id: str, image_id: str, prior: dict[str, Any] | None
) -> Image:
    """Attach (or clear) a PosePrior on an image. Pass ``None`` to clear."""
    img = await get_image(session, tenant_id=tenant_id, image_id=image_id)
    img.pose_prior_json = prior
    await session.flush()
    return img


async def list_pose_priors(
    session: AsyncSession, *, tenant_id: str, dataset_id: str
) -> list[tuple[Image, dict[str, Any]]]:
    """Return ``(image, prior_dict)`` for every image in the dataset that
    carries a non-null ``pose_prior_json``. Order: by image name."""
    rows = (
        (
            await session.execute(
                select(Image)
                .where(
                    Image.tenant_id == tenant_id,
                    Image.dataset_id == dataset_id,
                    Image.pose_prior_json.is_not(None),
                )
                .order_by(Image.name)
            )
        )
        .scalars()
        .all()
    )
    return [(img, img.pose_prior_json) for img in rows if img.pose_prior_json]


async def delete_image(
    session: AsyncSession, *, tenant_id: str, dataset_id: str, name: str
) -> None:
    result = await session.execute(
        select(Image).where(
            Image.tenant_id == tenant_id,
            Image.dataset_id == dataset_id,
            Image.name == name,
        )
    )
    img = result.scalar_one_or_none()
    if img is None:
        raise NotFoundError(f"Image {name} not found in dataset")
    await _delete_image_row(session, img)


async def delete_image_by_id(session: AsyncSession, *, tenant_id: str, image_id: str) -> None:
    img = await get_image(session, tenant_id=tenant_id, image_id=image_id)
    await _delete_image_row(session, img)


async def _delete_image_row(session: AsyncSession, img: Image) -> None:
    if img.source_kind == "upload":
        b = await session.get(Blob, img.content_sha)
        if b is not None:
            b.refcount = max(0, b.refcount - 1)
    await session.execute(delete(Image).where(Image.image_id == img.image_id))
    await recompute_manifest_hash(session, dataset_id=img.dataset_id)
