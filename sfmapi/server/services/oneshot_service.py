"""One-shot streaming services. Bytes-in / typed-result-out, no
persistence, no Job row, no cache. See
``docs/guides/oneshot_streaming_proposal.md``.
"""

from __future__ import annotations

import base64
import tempfile
import time
from pathlib import Path
from typing import Any

from sfm_hub.routing import ensure_provider_enabled
from sfmapi.server.adapters.backend import require_backend_method
from sfmapi.server.adapters.registry import get_backend
from sfmapi.server.core.errors import (
    CapabilityUnavailableError,
    NotFoundError,
    PycolmapUnavailableError,
    ValidationError,
)
from sfmapi.server.core.image_metadata import read_image_metadata, sniff_image_extension
from sfmapi.server.schemas.api.oneshot import (
    OneShotFeaturesPayload,
    OneShotFeaturesResponse,
    OneShotImageInfo,
    OneShotLocalizeResponse,
    OneShotRuntimeInfo,
)
from sfmapi.server.schemas.pipeline_spec import FeaturesSpec
from sfmapi.server.workers.options import stage_options

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


def _resolve_backend(provider: str | None = None) -> Any:
    try:
        if provider is not None:
            ensure_provider_enabled(provider)
        return get_backend(provider=provider)
    except KeyError as exc:
        raise ValidationError(str(exc)) from exc


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
    backend = _resolve_backend(spec.provider)
    feature_capability = f"features.extract.{spec.type}"

    metadata = read_image_metadata(image_bytes, content_type=content_type)

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
            extract_features = require_backend_method(
                backend,
                "extract_features",
                capability=feature_capability,
            )
            summary = extract_features(
                database_path=db_path,
                image_root=image_root,
                image_list=[image_file.name],
                options=_feature_options_from_spec(spec),
            )
        except CapabilityUnavailableError as exc:
            if exc.extras.get("capability") == "features.extract":
                raise CapabilityUnavailableError(capability=feature_capability) from exc
            raise
        except PycolmapUnavailableError:
            raise
        except Exception as e:
            raise ValidationError(f"oneshot/features: backend failed to extract: {e}") from e

        keypoints, descriptors_b64, descriptor_dim = _read_back_keypoints(
            db_path,
            image_file.name,
            backend=backend,
            feature_capability=feature_capability,
        )

    runtime_ms = int((time.perf_counter() - started) * 1000)
    return OneShotFeaturesResponse(
        image=OneShotImageInfo(
            width=metadata.width,
            height=metadata.height,
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
    return sniff_image_extension(b)


def _sift_options_from_spec(spec: FeaturesSpec) -> dict[str, Any]:
    """Translate FeaturesSpec into the dict shape
    ``backend.extract_features`` expects under ``options['sift']``."""
    out: dict[str, Any] = {
        "max_num_features": spec.max_num_features,
        "use_gpu": spec.use_gpu,
    }
    out.update(spec.backend_options or {})
    return out


def _feature_options_from_spec(spec: FeaturesSpec) -> dict[str, Any]:
    """Translate ``FeaturesSpec`` into the standard stage option envelope.

    One-shot feature calls should see the same typed envelope as worker-backed
    feature extraction while retaining the legacy ``options['sift']`` block for
    backends that still read pycolmap-style SIFT knobs from that location.
    """

    payload = spec.model_dump(mode="json", exclude_none=True)
    options = stage_options(payload)
    if "sift" not in options:
        options["sift"] = _sift_options_from_spec(spec)
    return options


def _read_back_keypoints(
    db_path: Path,
    image_name: str,
    *,
    backend: Any,
    feature_capability: str,
) -> tuple[list[list[float]], str, int]:
    """Read keypoints + descriptors back via the supplied backend.
    Returns:

      - ``keypoints``: list of [x, y, scale, angle] rows.
      - ``descriptors_b64``: base64-encoded float32 row-major.
      - ``descriptor_dim``: backend-reported descriptor width for the
        selected extractor.

    The oneshot path writes one image into ``db_path``; image_id is 1.
    Heavy-import isolation: this routes through the
    :class:`ObservationBackend.read_keypoints` Protocol method so service
    code never touches an engine library directly. ``backend`` is required
    — the caller must pass the same backend that performed extraction so
    a single oneshot call cannot silently split across two engines.
    """
    try:
        read_keypoints = require_backend_method(
            backend,
            "read_keypoints",
            capability=feature_capability,
        )
        keypoints, descriptors_bytes, descriptor_dim = read_keypoints(
            database_path=db_path,
            image_id=1,
        )
    except CapabilityUnavailableError:
        raise
    except PycolmapUnavailableError:
        raise
    descriptors_b64 = (
        base64.b64encode(descriptors_bytes).decode("ascii") if descriptors_bytes else ""
    )
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
    backend = _resolve_backend(spec.provider)
    metadata = read_image_metadata(image_bytes, content_type=content_type)

    with tempfile.TemporaryDirectory(prefix="sfmapi-oneshot-loc-") as tmp:
        tmp_path = Path(tmp)
        ext = _ext_for_content_type(content_type) or _sniff_extension(image_bytes) or ".jpg"
        query_path = tmp_path / f"query{ext}"
        query_path.write_bytes(image_bytes)

        try:
            localize_from_memory = require_backend_method(
                backend,
                "localize_from_memory",
                capability="localize.from_memory",
            )
            result_dict = localize_from_memory(
                sparse_dir=sparse_dir,
                query_image=query_path,
                spec=_feature_options_from_spec(spec),
            )
        except CapabilityUnavailableError:
            raise
        except PycolmapUnavailableError:
            raise
        except Exception as e:
            raise ValidationError(f"oneshot/localize: backend failed: {e}") from e

    runtime_ms = int((time.perf_counter() - started) * 1000)
    return OneShotLocalizeResponse(
        recon_id=recon_id,
        image=OneShotImageInfo(
            width=metadata.width,
            height=metadata.height,
            byte_size=len(image_bytes),
        ),
        result=result_dict,
        runtime=OneShotRuntimeInfo(backend=backend.name, ms=runtime_ms),
        spec=spec.model_dump(mode="json"),
    )
