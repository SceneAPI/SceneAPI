"""Dataset CRUD + manifest_hash recompute + ImageSource creation +
worker-derived dataset registration."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from sfmapi.server.core.config import get_settings
from sfmapi.server.core.errors import ConflictError, NotFoundError, ValidationError
from sfmapi.server.core.hashing import canonical_json, content_address, stream_sha256
from sfmapi.server.core.ids import new_id
from sfmapi.server.core.image_metadata import MAX_HEADER_SCAN_BYTES, read_image_metadata
from sfmapi.server.db.models import Dataset, Image, ImageSource, Job, Task
from sfmapi.server.db.pagination import paginate_keyset
from sfmapi.server.schemas.api.datasets import (
    LocalSourceSpec,
    S3SourceSpec,
    SourceSpec,
    UploadSourceSpec,
)
from sfmapi.server.sources.local import LocalPathSource


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
        from sfmapi.server.sources.s3 import S3Source

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
    from sfmapi.server.db.models import Blob

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


def _int_or_none(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _safe_relative_file(root: Path, rel_name: object) -> Path | None:
    if not isinstance(rel_name, str) or not rel_name:
        return None
    candidate = (root / rel_name).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


async def register_derived_dataset(
    session: AsyncSession, *, task: Task, outputs: dict[str, Any]
) -> None:
    """Register worker-generated image directories as datasets.

    Projection jobs emit a generic ``derived_dataset`` block in their
    task outputs. The dispatcher calls this on every task success; the
    block is turned into normal ImageSource, Dataset, and Image rows so
    downstream SfM stages can consume generated pixels without
    backend-specific bookkeeping. No-op when the outputs carry no
    (valid) ``derived_dataset`` block. Mutates the block in place with
    the registered ids; idempotent per task (re-runs reuse the rows).
    """
    raw = outputs.get("derived_dataset")
    if not isinstance(raw, dict):
        return
    root_raw = raw.get("root")
    if not isinstance(root_raw, str) or not root_raw:
        return
    root = Path(root_raw).resolve()
    settings = get_settings()
    workspace_root = settings.workspace_root.resolve()
    try:
        root.relative_to(workspace_root)
    except ValueError:
        return
    if not root.is_dir():
        return

    job = await session.get(Job, task.job_id)
    if job is None:
        return
    image_specs = raw.get("images")
    if not isinstance(image_specs, list):
        image_specs = []
    image_rows = [item for item in image_specs if isinstance(item, dict)]
    valid_images: list[tuple[dict[str, Any], Path, str]] = []
    for item in image_rows:
        rel_name = item.get("name")
        image_path = _safe_relative_file(root, rel_name)
        if image_path is not None and isinstance(rel_name, str):
            valid_images.append((cast(dict[str, Any], item), image_path, rel_name))
    if not valid_images:
        return

    existing = await _find_existing_derived_dataset(session, task=task, root=root)
    if existing is not None:
        dataset, source = existing
        registered_existing = await _derived_dataset_image_refs(
            session,
            tenant_id=task.tenant_id,
            dataset_id=dataset.dataset_id,
        )
        raw["name"] = dataset.name
        raw["dataset_id"] = dataset.dataset_id
        raw["project_id"] = dataset.project_id
        raw["source_id"] = source.source_id
        raw["registered_images"] = registered_existing
        raw["reused"] = True
        return

    source = ImageSource(
        tenant_id=task.tenant_id,
        kind="local",
        uri_or_root=str(root),
        fingerprint_json={
            "kind": "derived",
            "task_id": task.task_id,
            "job_id": task.job_id,
            "source_dataset_id": raw.get("source_dataset_id"),
            "image_names": sorted(rel_name for _, _, rel_name in valid_images),
        },
    )
    session.add(source)
    await session.flush()

    dataset_name = raw.get("name")
    if not isinstance(dataset_name, str) or not dataset_name.strip():
        dataset_name = f"{task.kind}-{task.task_id[:8]}"
    dataset_name = await _unique_derived_dataset_name(
        session,
        tenant_id=task.tenant_id,
        project_id=job.project_id,
        requested=dataset_name.strip(),
        task_id=task.task_id,
    )
    dataset = await create_dataset(
        session,
        tenant_id=task.tenant_id,
        project_id=job.project_id,
        source_id=source.source_id,
        name=dataset_name.strip(),
        camera_model=str(raw.get("camera_model") or "PINHOLE"),
        intrinsics_mode=str(raw.get("intrinsics_mode") or "per_image"),
        is_spherical=bool(raw.get("is_spherical", False)),
        rig_config=cast(dict[str, Any] | None, raw.get("rig_config"))
        if isinstance(raw.get("rig_config"), dict)
        else None,
    )

    # In-function import: image_service imports this module at load
    # time (recompute_manifest_hash), so the reverse import must be
    # deferred to call time.
    from sfmapi.server.services import image_service

    registered: list[dict[str, Any]] = []
    for item, image_path, rel_name in valid_images:
        with image_path.open("rb") as fp:
            content_sha, byte_size = stream_sha256(fp)
        with image_path.open("rb") as fp:
            metadata = read_image_metadata(fp.read(MAX_HEADER_SCAN_BYTES))
        image = await image_service.add_image(
            session,
            tenant_id=task.tenant_id,
            dataset=dataset,
            name=rel_name,
            content_sha=content_sha,
            source_kind="local",
            rel_path=rel_name,
            byte_size=byte_size,
            width=_int_or_none(item.get("width")) or metadata.width,
            height=_int_or_none(item.get("height")) or metadata.height,
        )
        registered.append(
            {
                "image_id": image.image_id,
                "name": image.name,
                "width": image.width,
                "height": image.height,
                "content_sha": image.content_sha,
            }
        )

    raw["name"] = dataset.name
    raw["dataset_id"] = dataset.dataset_id
    raw["project_id"] = job.project_id
    raw["source_id"] = source.source_id
    raw["registered_images"] = registered


async def _find_existing_derived_dataset(
    session: AsyncSession, *, task: Task, root: Path
) -> tuple[Dataset, ImageSource] | None:
    rows = (
        (
            await session.execute(
                select(ImageSource).where(
                    ImageSource.tenant_id == task.tenant_id,
                    ImageSource.kind == "local",
                    ImageSource.uri_or_root == str(root),
                )
            )
        )
        .scalars()
        .all()
    )
    for source in rows:
        fingerprint = source.fingerprint_json
        if not isinstance(fingerprint, dict) or fingerprint.get("task_id") != task.task_id:
            continue
        dataset = (
            (
                await session.execute(
                    select(Dataset).where(
                        Dataset.tenant_id == task.tenant_id,
                        Dataset.source_id == source.source_id,
                    )
                )
            )
            .scalars()
            .first()
        )
        if dataset is not None:
            return dataset, source
    return None


async def _derived_dataset_image_refs(
    session: AsyncSession, *, tenant_id: str, dataset_id: str
) -> list[dict[str, Any]]:
    images = (
        (
            await session.execute(
                select(Image).where(
                    Image.tenant_id == tenant_id,
                    Image.dataset_id == dataset_id,
                )
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "image_id": image.image_id,
            "name": image.name,
            "width": image.width,
            "height": image.height,
            "content_sha": image.content_sha,
        }
        for image in images
    ]


async def _unique_derived_dataset_name(
    session: AsyncSession,
    *,
    tenant_id: str,
    project_id: str,
    requested: str,
    task_id: str,
) -> str:
    base = requested[:255].strip() or f"project_images-{task_id[:8]}"
    existing = await _dataset_name_exists(
        session,
        tenant_id=tenant_id,
        project_id=project_id,
        name=base,
    )
    if not existing:
        return base
    suffix = f"-{task_id[:8]}"
    first = f"{base[: 255 - len(suffix)]}{suffix}"
    if not await _dataset_name_exists(
        session,
        tenant_id=tenant_id,
        project_id=project_id,
        name=first,
    ):
        return first
    for index in range(2, 1000):
        suffix = f"-{task_id[:8]}-{index}"
        candidate = f"{base[: 255 - len(suffix)]}{suffix}"
        if not await _dataset_name_exists(
            session,
            tenant_id=tenant_id,
            project_id=project_id,
            name=candidate,
        ):
            return candidate
    return f"{base[:239]}-{task_id[:8]}-{new_id()[:6]}"


async def _dataset_name_exists(
    session: AsyncSession, *, tenant_id: str, project_id: str, name: str
) -> bool:
    existing = (
        await session.execute(
            select(Dataset.dataset_id).where(
                Dataset.tenant_id == tenant_id,
                Dataset.project_id == project_id,
                Dataset.name == name,
            )
        )
    ).first()
    return existing is not None
