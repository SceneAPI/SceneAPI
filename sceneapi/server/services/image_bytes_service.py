"""Resolve image bytes + EXIF for any source kind."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sceneapi.server.core.errors import NotFoundError
from sceneapi.server.core.path_safety import resolve_under_root
from sceneapi.server.db.models import Dataset, Image, ImageSource
from sceneapi.server.storage.blobs import get_blob_store


async def resolve_image_path(session: AsyncSession, *, tenant_id: str, image: Image) -> Path:
    """Return a local filesystem path for the image's bytes.

    - ``source_kind=upload`` -> path inside the content-addressed blob
      store.
    - ``source_kind=local``  -> path under the source's root + ``rel_path``.
    - ``source_kind=s3``     -> raises (S3 sources are worker-side; the
      web tier does not materialize them on demand).
    """
    if image.source_kind == "upload":
        return get_blob_store().local_path(image.content_sha)
    if image.source_kind == "local":
        # Image rows don't carry source_id; look up via the dataset.
        ds = (
            await session.execute(select(Dataset).where(Dataset.dataset_id == image.dataset_id))
        ).scalar_one_or_none()
        if ds is None or ds.tenant_id != tenant_id:
            raise NotFoundError("dataset not found for image")
        src = await session.get(ImageSource, ds.source_id)
        if src is None or not src.uri_or_root:
            raise NotFoundError("local source has no root configured")
        rel = image.rel_path or image.name
        return resolve_under_root(src.uri_or_root, rel, field="rel_path")
    raise NotFoundError(f"image bytes not available for source_kind={image.source_kind}")


def extract_exif(path: Path) -> dict[str, Any]:
    """Best-effort EXIF extraction. Returns {} on any error."""
    try:
        from PIL import ExifTags
        from PIL import Image as PILImage
    except Exception:
        return {}
    try:
        with PILImage.open(path) as im:
            raw = im._getexif()
        if not raw:
            return {}
        out: dict[str, Any] = {}
        for tag, value in raw.items():
            name = ExifTags.TAGS.get(tag, str(tag))
            try:
                # Some EXIF values are bytes — decode safely.
                if isinstance(value, bytes):
                    value = value.decode("utf-8", errors="replace")
            except Exception:
                value = repr(value)
            out[name] = value
        return out
    except Exception:
        return {}
