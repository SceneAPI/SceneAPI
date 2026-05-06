"""One-shot streaming services. Bytes-in / typed-result-out, no
persistence, no Job row, no cache. See
``docs/guides/oneshot_streaming_proposal.md``.
"""

from __future__ import annotations

import base64
import struct
import tempfile
import time
from pathlib import Path
from typing import Any

from app.adapters.registry import get_backend
from app.core.errors import NotFoundError, PycolmapUnavailableError, ValidationError
from app.schemas.api.oneshot import (
    OneShotFeaturesPayload,
    OneShotFeaturesResponse,
    OneShotImageInfo,
    OneShotLocalizeResponse,
    OneShotRuntimeInfo,
)
from app.schemas.pipeline_spec import FeaturesSpec

# Marker the route handler can map to a 415 / 422 response. Real
# pycolmap accepts JPEG / PNG / TIFF / BMP / WebP; we only allow
# the formats colmap_mod has been compiled to read.
_ACCEPTED_CONTENT_TYPES = {
    "image/jpeg",
    "image/png",
    "image/tiff",
    "image/bmp",
    "image/webp",
    "application/octet-stream",  # generic fallback when client doesn't set CT
}


def extract_features_oneshot(
    image_bytes: bytes,
    spec: FeaturesSpec,
    *,
    content_type: str = "application/octet-stream",
) -> OneShotFeaturesResponse:
    """Run SIFT (or whatever ``spec.type`` selects) on a single
    in-memory image and return the keypoints + descriptors inline.

    No DB writes, no Image / Blob / Job rows. The image bytes are
    written to a tempfile only because the pycolmap binding wants a
    path; the tempfile is deleted before this function returns.

    Raises:
      ValidationError: empty body, bad image, unsupported content type.
      PycolmapUnavailableError: server has no pycolmap installed.
    """
    if not image_bytes:
        raise ValidationError("oneshot/features: empty request body")
    if content_type not in _ACCEPTED_CONTENT_TYPES:
        raise ValidationError(
            f"oneshot/features: unsupported content type {content_type!r}; "
            f"accepted: {sorted(_ACCEPTED_CONTENT_TYPES)}"
        )

    started = time.perf_counter()
    backend = get_backend()

    # Resolve image dimensions cheaply (no pycolmap dependency for the
    # echo-back fields). PIL is a soft dep already pulled in by other
    # workers; if it's missing we fall back to (-1, -1).
    width, height = _try_decode_dimensions(image_bytes)

    # Build a tempdir scoped to this request. The directory is deleted
    # automatically when the context manager exits — this is the
    # entire "persistence" footprint.
    with tempfile.TemporaryDirectory(prefix="sfmapi-oneshot-") as tmp:
        tmp_path = Path(tmp)
        # pycolmap's extract_features takes (database, image_root, names).
        # We arrange a one-element "dataset" inside the tempdir.
        image_root = tmp_path / "images"
        image_root.mkdir()
        # The filename extension MUST match the bytes; pycolmap reads
        # it via OpenCV's image-format detection. We default to .jpg
        # when the content_type is generic.
        ext = _ext_for_content_type(content_type) or _sniff_extension(image_bytes) or ".jpg"
        image_file = image_root / f"oneshot{ext}"
        image_file.write_bytes(image_bytes)

        db_path = tmp_path / "oneshot.db"
        # Reuse the same backend method the resource API uses, on a
        # tempdir database. Per-call cost is the database create +
        # extract; both are dominated by the SIFT GPU work.
        try:
            summary = backend.extract_features(
                database_path=db_path,
                image_root=image_root,
                image_list=[image_file.name],
                options={"sift": _sift_options_from_spec(spec)},
            )
        except PycolmapUnavailableError:
            raise
        except Exception as e:
            raise ValidationError(f"oneshot/features: backend failed to extract: {e}") from e

        keypoints, descriptors_b64, descriptor_dim = _read_back_keypoints(db_path, image_file.name)

    runtime_ms = int((time.perf_counter() - started) * 1000)
    return OneShotFeaturesResponse(
        image=OneShotImageInfo(
            width=width,
            height=height,
            byte_size=len(image_bytes),
        ),
        features=OneShotFeaturesPayload(
            type=spec.type,
            count=summary.get("num_keypoints", len(keypoints)),
            descriptor_dim=descriptor_dim,
            keypoints=keypoints,
            descriptors_b64=descriptors_b64,
        ),
        runtime=OneShotRuntimeInfo(backend=backend.name, ms=runtime_ms),
        spec=spec.model_dump(mode="json"),
    )


# --- helpers --------------------------------------------------------


def _try_decode_dimensions(image_bytes: bytes) -> tuple[int, int]:
    """Best-effort image dimension decode via PIL. Returns (-1, -1)
    when PIL is unavailable or the bytes don't decode."""
    try:
        import io

        from PIL import Image as _PILImage  # type: ignore[import-not-found]
    except ImportError:
        return (-1, -1)
    try:
        with _PILImage.open(io.BytesIO(image_bytes)) as im:
            return (int(im.width), int(im.height))
    except Exception:
        return (-1, -1)


def _ext_for_content_type(content_type: str) -> str | None:
    return {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/tiff": ".tif",
        "image/bmp": ".bmp",
        "image/webp": ".webp",
    }.get(content_type)


def _sniff_extension(b: bytes) -> str | None:
    """Magic-byte sniff — only used when the consumer didn't set a
    helpful Content-Type."""
    if len(b) < 8:
        return None
    if b[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if b[:4] in (b"II*\x00", b"MM\x00*"):
        return ".tif"
    if b[:2] == b"BM":
        return ".bmp"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return ".webp"
    return None


def _sift_options_from_spec(spec: FeaturesSpec) -> dict[str, Any]:
    """Translate FeaturesSpec into the dict shape
    ``backend.extract_features`` expects under ``options['sift']``."""
    out: dict[str, Any] = {
        "max_num_features": (
            spec.sift_max_num_features
            if spec.sift_max_num_features is not None
            else spec.max_num_features
        ),
        "use_gpu": spec.use_gpu,
    }
    if spec.sift_first_octave is not None:
        out["first_octave"] = spec.sift_first_octave
    out.update(spec.extractor_options or {})
    return out


def _read_back_keypoints(db_path: Path, image_name: str) -> tuple[list[list[float]], str, int]:
    """Read keypoints + descriptors back out of the COLMAP database
    for one image. Returns:

      - ``keypoints``: list of [x, y, scale, angle] (4-element rows).
      - ``descriptors_b64``: base64-encoded float32 row-major.
      - ``descriptor_dim``: 128 for SIFT.

    pycolmap stores keypoints as float32 (N, 6) [x, y, a11, a12, a21,
    a22] when the affine extractor is used; for SIFT the layout is
    (N, 4) [x, y, scale, angle]. We read the 4-column variant; if the
    column count differs, we keep only the first 4 columns.
    """
    try:
        import pycolmap as pc  # type: ignore[import-not-found]
    except ImportError as e:
        raise PycolmapUnavailableError(
            "oneshot/features requires pycolmap to read keypoints back from the temp database"
        ) from e

    keypoints: list[list[float]] = []
    descriptors_b64 = ""
    descriptor_dim = 128
    with pc.Database(str(db_path)) as db:
        # pycolmap exposes images by id; one image, id == 1.
        for image_id in db.read_all_images():
            kp = db.read_keypoints(image_id.image_id)  # numpy (N, K)
            desc = db.read_descriptors(image_id.image_id)  # numpy (N, D) uint8
            if kp.size:
                arr = kp.tolist()
                keypoints = [list(row[:4]) for row in arr]
            if desc.size:
                # Encode as float32 to match the OneShotFeaturesPayload
                # contract (descriptors_b64 is float32 row-major).
                desc_f = desc.astype("float32", copy=False)
                descriptor_dim = int(desc_f.shape[1])
                descriptors_b64 = base64.b64encode(desc_f.tobytes()).decode("ascii")
            break  # one image only
    return keypoints, descriptors_b64, descriptor_dim


def localize_oneshot(
    image_bytes: bytes,
    recon_id: str,
    spec: FeaturesSpec,
    *,
    sparse_dir: Path,
    content_type: str = "application/octet-stream",
) -> OneShotLocalizeResponse:
    """Localize a single in-memory query image against an existing
    reconstruction's sealed sparse directory. No DB row, no Job row,
    no Upload row — the query image hits a tempfile only because the
    pycolmap binding wants a path; the tempfile is deleted before
    this function returns.

    The ``sparse_dir`` argument is supplied by the route handler
    after it has resolved ``recon_id`` against the tenant via the
    standard tenancy machinery, so this function has no DB
    dependency itself.

    Raises:
      ValidationError: empty body or unsupported content type.
      NotFoundError: ``sparse_dir`` does not exist on disk
        (recon hasn't been mapped yet).
      PycolmapUnavailableError: server has no pycolmap installed.
    """
    if not image_bytes:
        raise ValidationError("oneshot/localize: empty request body")
    if content_type not in _ACCEPTED_CONTENT_TYPES:
        raise ValidationError(
            f"oneshot/localize: unsupported content type {content_type!r}; "
            f"accepted: {sorted(_ACCEPTED_CONTENT_TYPES)}"
        )
    if not sparse_dir.is_dir():
        raise NotFoundError(
            f"oneshot/localize: sparse dir for recon {recon_id} not on "
            f"disk yet — has the mapping stage run?"
        )

    started = time.perf_counter()
    backend = get_backend()
    width, height = _try_decode_dimensions(image_bytes)

    with tempfile.TemporaryDirectory(prefix="sfmapi-oneshot-loc-") as tmp:
        tmp_path = Path(tmp)
        ext = _ext_for_content_type(content_type) or _sniff_extension(image_bytes) or ".jpg"
        query_path = tmp_path / f"query{ext}"
        query_path.write_bytes(image_bytes)

        try:
            result_dict = backend.localize_from_memory(
                sparse_dir=sparse_dir,
                query_image=query_path,
                spec={"sift": _sift_options_from_spec(spec)},
            )
        except PycolmapUnavailableError:
            raise
        except Exception as e:
            raise ValidationError(f"oneshot/localize: backend failed: {e}") from e

    runtime_ms = int((time.perf_counter() - started) * 1000)
    return OneShotLocalizeResponse(
        recon_id=recon_id,
        image=OneShotImageInfo(width=width, height=height, byte_size=len(image_bytes)),
        result=result_dict,
        runtime=OneShotRuntimeInfo(backend=backend.name, ms=runtime_ms),
        spec=spec.model_dump(mode="json"),
    )


# Defensive type-check helper used by the ``_read_back_keypoints``
# tests; not part of the public surface.
def _struct_unused() -> None:
    _ = struct.pack  # silence "unused import" — struct is here for future
    _ = struct  # binary re-encoding work documented in P4 phase a.
