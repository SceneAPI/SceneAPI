"""Health, readiness, version, metrics."""

from __future__ import annotations

import contextlib

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from sfmapi.server import __version__
from sfmapi.server.core.config import get_settings
from sfmapi.server.db.session import get_db
from sfmapi.server.schemas.api.common import (
    HealthResponse,
    ReadyzResponse,
    SpecResponse,
    SpecServerInfo,
    VersionResponse,
)

router = APIRouter(tags=["health"])


@router.get("/healthz", response_model=HealthResponse)
async def healthz() -> HealthResponse:
    """Liveness — only checks the process is responding. Use
    ``/readyz`` to test that the service can serve traffic."""
    return HealthResponse(status="ok")


@router.get(
    "/readyz",
    response_model=ReadyzResponse,
    responses={503: {"model": ReadyzResponse}},
)
async def readyz(session: AsyncSession = Depends(get_db)) -> JSONResponse:
    """Readiness — verifies backing stores (DB; Redis when configured)
    are reachable. Returns ``503`` with a per-check breakdown when
    anything is unreachable so Kubernetes / load balancers can drain
    traffic during a degraded state."""
    checks: dict[str, str] = {}
    overall_ok = True
    try:
        await session.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as e:
        checks["db"] = f"unreachable: {e}"
        overall_ok = False
    settings = get_settings()
    if getattr(settings, "redis_url", None) and settings.queue_backend != "inline":
        from sfmapi.server.orchestrator.queue import get_queue

        queue = get_queue(settings)
        try:
            if await queue.health():
                checks["queue"] = "ok"
            else:
                checks["queue"] = "unreachable"
                overall_ok = False
        except Exception as e:
            checks["queue"] = f"unreachable: {e}"
            overall_ok = False
        finally:
            with contextlib.suppress(Exception):
                await queue.close()
    body = ReadyzResponse(status="ok" if overall_ok else "degraded", checks=checks)
    code = status.HTTP_200_OK if overall_ok else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(body.model_dump(), status_code=code)


@router.get("/version", response_model=VersionResponse)
async def version() -> VersionResponse:
    """Return sfmapi + the registered backend's identity / runtime
    version map. ``backend`` is ``None`` when no backend is registered
    — useful for headless / wire-only deployments.

    The contents of ``backend.runtime_versions`` are backend-defined;
    typical fields include engine commit shas, CUDA arch, and
    auxiliary library versions that influence the cache-key salt.
    """
    from sfmapi.server.adapters.backend import Backend
    from sfmapi.server.adapters.registry import get_backend
    from sfmapi.server.schemas.api.common import BackendVersion

    backend_info: BackendVersion | None = None
    try:
        backend: Backend = get_backend()
    except KeyError:
        backend = None  # type: ignore[assignment]
    if backend is not None:
        backend_info = BackendVersion(
            name=backend.name,
            version=backend.version,
            vendor=getattr(backend, "vendor", None),
            runtime_versions=dict(backend.runtime_versions()),
        )
    return VersionResponse(sfmapi=__version__, backend=backend_info)


@router.get("/spec", response_model=SpecResponse)
async def spec() -> SpecResponse:
    """Discovery endpoint: identifies which standard this server
    implements. Clients can hit this to learn the spec version + a
    pointer to the human-readable doc.

    ``spec_url`` is configurable via ``SFMAPI_SPEC_URL`` because sfmapi
    has no canonical hosting; deployments point clients at their own
    spec mirror or leave it ``None``."""
    settings = get_settings()
    return SpecResponse(
        spec="sfmapi",
        spec_version="v1.0-draft",
        spec_url=settings.spec_url,
        openapi_url="/openapi.json",
        server=SpecServerInfo(name="sfmapi-reference", version=__version__),
    )


@router.get("/openapi.json", include_in_schema=False)
async def openapi_json(request: Request) -> JSONResponse:
    """Mirror of FastAPI's built-in `/openapi.json` for stable
    discoverability and so SDK generators can fetch a known path.

    The payload is the same OpenAPI document FastAPI normally serves
    at `/openapi.json` (which is also still available); we re-export
    it here under `/v1/openapi.json`-compatible semantics later if we
    ever shard versions.
    """
    return JSONResponse(request.app.openapi())


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    """Prometheus exposition endpoint.

    ``include_in_schema=False`` keeps it out of the SDK-facing
    OpenAPI surface — scrapers hit it directly. Best-effort gauge
    refresh runs first; storage errors are swallowed so a transient
    DB hiccup never trips the scrape.
    """
    import contextlib

    from sqlalchemy import func, select

    from sfmapi.server.core import metrics as m
    from sfmapi.server.db.models import Task
    from sfmapi.server.db.session import get_session_factory

    # Best-effort gauge refresh; never let /metrics 500 on db hiccups.
    with contextlib.suppress(Exception):
        factory = get_session_factory()
        async with factory() as session:
            rows = (
                await session.execute(
                    select(Task.kind, func.count(Task.task_id))
                    .where(Task.status == "pending")
                    .group_by(Task.kind)
                )
            ).all()
            seen_kinds: set[str] = set()
            for kind, count in rows:
                m.queue_depth.labels(kind=kind).set(int(count))
                seen_kinds.add(kind)
    # Touch each canonical series so /metrics always exposes them.
    m.queue_depth.labels(kind="extract")
    m.active_jobs.labels(tenant_id="default")
    m.storage_bytes.labels(tenant_id="default")
    m.worker_lease_age_seconds.labels(worker_id="bootstrap")
    m.errors_total.labels(error_class="bootstrap")
    m.task_duration_seconds.labels(kind="bootstrap", outcome="ok")
    m.job_duration_seconds.labels(recipe="bootstrap", outcome="ok")

    body, ctype = m.render()
    return Response(content=body, media_type=ctype)
