"""Dataset CRUD + manifest_hash recompute + ImageSource creation."""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.hashing import canonical_json, content_address
from app.db.models import Dataset, Image, ImageSource
from app.db.pagination import paginate_keyset
from app.schemas.api.datasets import (
    LocalSourceSpec,
    S3SourceSpec,
    SourceSpec,
    UploadSourceSpec,
)
from app.sources.local import LocalPathSource


async def create_image_source(
    session: AsyncSession, *, tenant_id: str, source: SourceSpec
) -> ImageSource:
    """Persist an ImageSource row from a request-side source spec."""
    if isinstance(source, UploadSourceSpec):
        fp = {
            "kind": "upload",
            "entries": sorted([(e.name, e.blob_sha) for e in source.entries]),
        }
        src = ImageSource(tenant_id=tenant_id, kind="upload", uri_or_root=None, fingerprint_json=fp)
    elif isinstance(source, LocalSourceSpec):
        local = LocalPathSource(root=source.root, recursive=source.recursive)
        src = ImageSource(
            tenant_id=tenant_id,
            kind="local",
            uri_or_root=str(local.root.resolve()),
            fingerprint_json=local.fingerprint(),
        )
    elif isinstance(source, S3SourceSpec):
        from app.sources.s3 import S3Source

        s3 = S3Source(bucket=source.bucket, prefix=source.prefix)
        try:
            fp = s3.fingerprint()
        except Exception as e:
            raise ValidationError(f"S3 fingerprint failed: {e}") from e
        src = ImageSource(
            tenant_id=tenant_id,
            kind="s3",
            uri_or_root=f"s3://{source.bucket}/{source.prefix}",
            fingerprint_json=fp,
        )
    else:
        raise ValidationError("Unknown source kind")
    session.add(src)
    await session.flush()
    return src


async def create_dataset(
    session: AsyncSession,
    *,
    tenant_id: str,
    project_id: str,
    source_id: str,
    name: str,
    camera_model: str = "SIMPLE_RADIAL",
    intrinsics_mode: str = "single_camera",
    is_spherical: bool = False,
    rig_config: dict | None = None,
    respect_exif_orientation: bool = False,
) -> Dataset:
    d = Dataset(
        tenant_id=tenant_id,
        project_id=project_id,
        source_id=source_id,
        name=name,
        camera_model=camera_model,
        intrinsics_mode=intrinsics_mode,
        is_spherical=is_spherical,
        rig_config_json=rig_config,
        respect_exif_orientation=respect_exif_orientation,
        manifest_hash="",  # recomputed on first image add
    )
    session.add(d)
    try:
        await session.flush()
    except IntegrityError as e:
        await session.rollback()
        raise ConflictError(f"Dataset {name!r} already exists in project") from e
    return d


async def get_dataset(
    session: AsyncSession,
    *,
    tenant_id: str,
    dataset_id: str,
    project_id: str | None = None,
) -> Dataset:
    """Load a Dataset by id, scoped to ``tenant_id``.

    Pass ``project_id`` to additionally enforce that the row belongs
    to that project — raises :class:`ValidationError` on mismatch.
    Routes nested under ``/projects/{pid}/datasets/{did}`` should pass
    it; top-level reads (``/v1/datasets/{did}/...``) leave it ``None``.
    """
    result = await session.execute(
        select(Dataset).where(Dataset.tenant_id == tenant_id, Dataset.dataset_id == dataset_id)
    )
    d = result.scalar_one_or_none()
    if d is None:
        raise NotFoundError(f"Dataset {dataset_id} not found")
    if project_id is not None and d.project_id != project_id:
        raise ValidationError("Dataset does not belong to project")
    return d


async def list_datasets(
    session: AsyncSession,
    *,
    tenant_id: str,
    project_id: str,
    page_size: int = 100,
    page_token: str | None = None,
) -> tuple[list[Dataset], str | None]:
    """AIP-158 keyset pagination on ``dataset_id`` ascending."""
    stmt = select(Dataset).where(Dataset.tenant_id == tenant_id, Dataset.project_id == project_id)
    return await paginate_keyset(
        session,
        stmt,
        pk=Dataset.dataset_id,
        page_size=page_size,
        page_token=page_token,
    )


async def delete_dataset(session: AsyncSession, *, tenant_id: str, dataset_id: str) -> None:
    """Cascade-delete a dataset and its images. Blob refcounts are
    decremented for upload-sourced images. Caller is responsible for
    workspace cleanup if any."""
    d = await get_dataset(session, tenant_id=tenant_id, dataset_id=dataset_id)
    # Decrement blob refcounts before deleting Image rows.
    from app.db.models import Blob

    rows = (
        (
            await session.execute(
                select(Image).where(Image.tenant_id == tenant_id, Image.dataset_id == d.dataset_id)
            )
        )
        .scalars()
        .all()
    )
    for img in rows:
        if img.source_kind == "upload":
            b = await session.get(Blob, img.content_sha)
            if b is not None:
                b.refcount = max(0, b.refcount - 1)
    await session.execute(delete(Image).where(Image.dataset_id == d.dataset_id))
    await session.execute(delete(Dataset).where(Dataset.dataset_id == d.dataset_id))


async def patch_dataset(
    session: AsyncSession,
    *,
    tenant_id: str,
    dataset_id: str,
    updates: dict,
) -> Dataset:
    if not updates:
        return await get_dataset(session, tenant_id=tenant_id, dataset_id=dataset_id)
    d = await get_dataset(session, tenant_id=tenant_id, dataset_id=dataset_id)
    field_aliases = {"rig_config": "rig_config_json"}
    allowed = {
        "name",
        "camera_model",
        "intrinsics_mode",
        "is_spherical",
        "rig_config",
        "respect_exif_orientation",
        "active_maskset_id",
    }
    for k, v in updates.items():
        if k not in allowed:
            continue
        attr = field_aliases.get(k, k)
        setattr(d, attr, v)
    try:
        await session.flush()
    except IntegrityError as e:
        await session.rollback()
        raise ConflictError("Dataset name conflict on update") from e
    return d


async def recompute_manifest_hash(session: AsyncSession, *, dataset_id: str) -> str:
    result = await session.execute(
        select(Image.name, Image.content_sha)
        .where(Image.dataset_id == dataset_id)
        .order_by(Image.name)
    )
    pairs = [(name, sha) for (name, sha) in result.all()]
    payload = {"images": pairs}
    h = content_address(canonical_json(payload))
    await session.execute(
        Dataset.__table__.update().where(Dataset.dataset_id == dataset_id).values(manifest_hash=h)
    )
    return h
