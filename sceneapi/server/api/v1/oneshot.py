"""One-shot streaming endpoints — see
``docs/guides/oneshot_streaming_proposal.md``.

Each route accepts image bytes in the request body, dispatches
inline, and returns a typed result. **No DB row, no persisted
blob, no Job, no sealed snapshot, no sequence number.** The
existence of these endpoints is what consumers should reach for
when they want "give me features from one image, right now"
instead of the eight-step resource-API setup.

P4 Phase a: ``POST /v1/oneshot/features``.
"""

from __future__ import annotations

from functools import partial

import anyio.to_thread
from fastapi import APIRouter, Depends, Header, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from sceneapi.server.core.config import get_settings
from sceneapi.server.core.errors import QuotaExceededError
from sceneapi.server.core.paths import Paths
from sceneapi.server.core.tenancy import current_tenant
from sceneapi.server.db.session import get_db
from sceneapi.server.schemas.api.oneshot import OneShotFeaturesResponse, OneShotLocalizeResponse
from sceneapi.server.schemas.pipeline_spec import FeaturesSpec, FeatureType
from sceneapi.server.services import oneshot_service, reconstruction_service

router = APIRouter(prefix="/oneshot", tags=["oneshot"])


@router.post(
    "/features",
    response_model=OneShotFeaturesResponse,
)
async def oneshot_features(
    request: Request,
    type: FeatureType = Query("sift", description="Local feature extractor."),
    provider: str | None = Query(None, description="Optional provider id to execute this call."),
    max_num_features: int = Query(8192, ge=1, le=65536),
    use_gpu: bool = Query(True),
    seed: int = Query(0),
    content_type: str | None = Header(None, alias="Content-Type"),
    tenant_id: str = Depends(current_tenant),  # auth only; no rows scoped
) -> OneShotFeaturesResponse:
    """Extract local features from a single image. Bytes-in /
    typed-result-out. No persistence.

    Mirrors the parameter set of :class:`FeaturesSpec`. The image
    bytes are read from the request body; the ``Content-Type`` header
    is used to choose a tempfile extension if present, else the
    bytes are sniffed.

    Returns the keypoints + base64-encoded float32 descriptors
    inline. For batch / multi-image / multi-stage flows, use the
    resource API instead.
    """
    settings = get_settings()
    body = await request.body()
    cap = settings.oneshot_max_request_bytes
    if cap > 0 and len(body) > cap:
        raise QuotaExceededError(
            f"oneshot/features: request body {len(body)} bytes exceeds "
            f"oneshot_max_request_bytes={cap}"
        )

    # Even though tenant scoping isn't applied to a row, calling the
    # dep enforces auth + opens a hook for future per-tenant rate
    # limits keyed on `tenant_id`.
    _ = tenant_id

    spec = FeaturesSpec(
        type=type,
        provider=provider,
        max_num_features=max_num_features,
        use_gpu=use_gpu,
        seed=seed,
    )
    # Engine-bound CPU work — run it in a worker thread so a slow
    # oneshot request doesn't block the event loop (e.g. /healthz).
    return await anyio.to_thread.run_sync(
        partial(
            oneshot_service.extract_features_oneshot,
            body,
            spec,
            content_type=content_type or "application/octet-stream",
        )
    )


@router.post(
    "/localize",
    response_model=OneShotLocalizeResponse,
)
async def oneshot_localize(
    request: Request,
    recon_id: str = Query(..., description="Existing reconstruction to localize against."),
    type: FeatureType = Query("sift", description="Local feature extractor."),
    provider: str | None = Query(None, description="Optional provider id to execute this call."),
    max_num_features: int = Query(8192, ge=1, le=65536),
    use_gpu: bool = Query(True),
    seed: int = Query(0),
    content_type: str | None = Header(None, alias="Content-Type"),
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> OneShotLocalizeResponse:
    """Localize a single image against an existing reconstruction.
    Bytes-in / typed-result-out. No persistence.

    Collapses the eight-step "upload + register + submit-localize +
    poll-job + decode" flow to one HTTP request. The query image is
    held in memory and (briefly) on disk in a tempdir for pycolmap;
    no Image / Blob / Upload / Job row is created.
    """
    settings = get_settings()
    body = await request.body()
    cap = settings.oneshot_max_request_bytes
    if cap > 0 and len(body) > cap:
        raise QuotaExceededError(
            f"oneshot/localize: request body {len(body)} bytes exceeds "
            f"oneshot_max_request_bytes={cap}"
        )

    # Resolve the reconstruction → sparse_dir under the tenant.
    # ``get_reconstruction`` raises NotFoundError if the recon
    # doesn't exist for this tenant — same error semantics as the
    # resource-API ``POST /v1/reconstructions/{rid}/localize``.
    r = await reconstruction_service.get_reconstruction(
        session, tenant_id=tenant_id, recon_id=recon_id
    )
    paths = Paths(settings)
    sparse_dir = paths.reconstruction_root(tenant_id, r.project_id, r.recon_id) / "sparse"

    spec = FeaturesSpec(
        type=type,
        provider=provider,
        max_num_features=max_num_features,
        use_gpu=use_gpu,
        seed=seed,
    )
    # Engine-bound CPU work — run it in a worker thread so a slow
    # oneshot request doesn't block the event loop (e.g. /healthz).
    return await anyio.to_thread.run_sync(
        partial(
            oneshot_service.localize_oneshot,
            body,
            recon_id=recon_id,
            spec=spec,
            sparse_dir=sparse_dir,
            content_type=content_type or "application/octet-stream",
        )
    )
