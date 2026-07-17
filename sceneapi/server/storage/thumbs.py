"""Thumbnail rendering + on-disk cache.

Thumbnails are addressed by `(content_sha, max_size)` — same blob,
same requested size = same JPEG. The cache lives at
`<workspace_root>/_thumbs/<sha>_<size>.jpg` and is treated as an
opaque content-derived file (mtime is irrelevant; the cache key
already encodes the inputs).
"""

from __future__ import annotations

import io
from pathlib import Path

from sceneapi.server.core.config import get_settings
from sceneapi.server.core.errors import CapabilityUnavailableError


def thumb_path(sha: str, size: int) -> Path:
    return get_settings().workspace_root / "_thumbs" / f"{sha}_{size}.jpg"


def make_thumbnail(src: Path, size: int) -> bytes:
    """Render a JPEG thumbnail no larger than `size` x `size` (preserves
    aspect). Always strips EXIF orientation so the resized image isn't
    sideways."""
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:
        raise CapabilityUnavailableError(
            capability="images.thumbnail",
            reason="thumbnail rendering requires the optional Pillow dependency",
        ) from exc

    with Image.open(src) as opened:
        im = ImageOps.exif_transpose(opened)
        im.thumbnail((size, size), Image.Resampling.LANCZOS)
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=82, optimize=True)
        return buf.getvalue()


def get_or_create(src: Path, sha: str, size: int) -> Path:
    """Returns the on-disk path of the cached thumbnail, rendering it
    on demand if missing. The `sha` is the **source image** sha, used
    as the cache key, NOT the thumbnail's own sha."""
    out = thumb_path(sha, size)
    if out.is_file():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    data = make_thumbnail(src, size)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_bytes(data)
    import os

    os.replace(tmp, out)
    return out
