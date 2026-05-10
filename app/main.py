"""FastAPI app entrypoint.

Critical contract: importing this module must not import `pycolmap`,
`torch`, `cv2`, or any other heavy dep. Heavy deps live behind
`app.adapters.*` and are imported only inside worker processes.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app import __version__
from app.api.v1 import health
from app.core.config import get_settings
from app.core.errors import SfmApiError
from app.core.ids import new_id
from app.core.logging import bind_request_context, configure_logging, get_logger
from app.core.profiling import RequestProfilingMiddleware

REQUEST_ID_HEADER = "X-Request-ID"


def _request_validation_errors_for_wire(exc: RequestValidationError) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    for error in exc.errors():
        item = dict(error)
        ctx = item.get("ctx")
        if isinstance(ctx, dict):
            item["ctx"] = {
                str(key): str(value) if isinstance(value, BaseException) else value
                for key, value in ctx.items()
            }
        errors.append(item)
    return errors


class RequestIdMiddleware(BaseHTTPMiddleware):
    """AIP-155 request correlation middleware.

    Reads the inbound ``X-Request-ID`` header (any non-empty value);
    falls back to a fresh ULID via :func:`app.core.ids.new_id` when
    absent. Echoes the resolved id in the response header so clients
    can stitch their logs to ours, and binds it (plus the resolved
    tenant_id when already on the request scope) onto
    :func:`structlog.contextvars` for the lifetime of the request via
    :func:`app.core.logging.bind_request_context`.

    Wired before any router so every handler — including streaming
    responses (SSE / file downloads) — sees the bound context.
    """

    async def dispatch(self, request: Request, call_next):  # type: ignore[override]
        incoming = request.headers.get(REQUEST_ID_HEADER, "").strip()
        request_id = incoming or new_id()
        request.state.request_id = request_id
        with bind_request_context(request_id=request_id):
            response: Response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    settings.ensure_dirs()
    log = get_logger("sfmapi.startup")
    log.info(
        "sfmapi.start",
        env=settings.env,
        version=__version__,
        ephemeral=settings.ephemeral,
        blob_backend=settings.blob_backend,
        queue_backend=settings.queue_backend,
    )
    if settings.ephemeral:
        # Bootstrap schema in the in-memory DB and remember the
        # workspace tempdir so we can wipe it on shutdown. Models are
        # imported lazily so the web process stays light when this
        # branch is unused.
        from app.db import models as _models  # noqa: F401 — register tables
        from app.db.base import Base
        from app.db.session import get_engine

        engine = get_engine(settings)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        # Ephemeral mode is for demos / embedded use / smoke tests
        # — register the no-op stub backend so workers can produce
        # terminal task statuses (most ops raise
        # CapabilityUnavailableError, which is fine for the
        # protocol-shape coverage these modes care about).
        import os

        from app.adapters.registry import register_backend
        from app.adapters.stub_backend import StubBackend

        register_backend("stub", StubBackend)
        os.environ.setdefault("SFMAPI_BACKEND", "stub")
        log.info("sfmapi.ephemeral_bootstrapped", workspace=str(settings.workspace_root))
    if settings.warm_capabilities:
        try:
            from app.core.capabilities import detect_capabilities

            detect_capabilities()
            log.info("sfmapi.capabilities_warmed")
        except Exception as exc:
            log.warning("sfmapi.capabilities_warm_failed", error=str(exc))
    try:
        yield
    finally:
        if settings.ephemeral:
            from app.db.session import _engine as _shared_engine
            from app.storage.blobs import reset_memory_blob_store_for_tests

            if _shared_engine is not None:
                await _shared_engine.dispose()
            reset_memory_blob_store_for_tests()
            import shutil

            shutil.rmtree(settings.workspace_root, ignore_errors=True)
            log.info("sfmapi.ephemeral_cleaned", workspace=str(settings.workspace_root))
        log.info("sfmapi.stop")


def create_app() -> FastAPI:
    settings = get_settings()
    app_lifespan = lifespan
    mcp_app = None
    if settings.mcp_api_enabled():
        from app.mcp.server import create_mcp_server

        mount_path = settings.normalized_mcp_mount_path()
        mcp_asgi = create_mcp_server(
            include_index_route=False,
            endpoint_hint=mount_path,
        ).http_app(path="/")
        mcp_app = mcp_asgi

        @asynccontextmanager
        async def combined_lifespan(app: FastAPI) -> AsyncIterator[None]:
            async with lifespan(app), mcp_asgi.lifespan(app):
                yield

        app_lifespan = combined_lifespan

    app = FastAPI(
        title="sfmapi",
        version=__version__,
        lifespan=app_lifespan,
    )

    origins = settings.cors_origin_list()
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=settings.cors_allow_credentials,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=[
                "ETag",
                "Last-Modified",
                "Content-Range",
                "Location",
                "Link",
                REQUEST_ID_HEADER,
                "Server-Timing",
            ],
        )

    if settings.profile_requests:
        app.add_middleware(RequestProfilingMiddleware)

    # Request correlation must be the outermost user middleware so
    # the bound context covers every handler / exception handler and
    # the echoed header survives CORS / GZip wrapping.
    app.add_middleware(RequestIdMiddleware)

    @app.exception_handler(SfmApiError)
    async def sfmapi_error_handler(request: Request, exc: SfmApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.as_problem(instance=str(request.url)),
            media_type="application/problem+json",
        )

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """Wrap FastAPI's Pydantic validation errors in the RFC 7807
        ``problem+json`` envelope so the wire shape stays consistent
        with every domain error from :class:`SfmApiError`. Without
        this handler, FastAPI emits ``{"detail": [{loc, msg, type}]}``
        which fails the SDK ergonomics' ``raise_for_status`` parsing.

        The structured Pydantic field errors are preserved under the
        ``errors`` key so machine-readable consumers (form-validators,
        OpenAPI explorers) can still surface them per-field.
        """
        errors = _request_validation_errors_for_wire(exc)
        # Build a short human summary so the ``detail`` field is not
        # an empty string. Take up to three field problems and join.
        first = errors[:3]
        summary = "; ".join(
            f"{'.'.join(str(p) for p in e.get('loc', []))}: {e.get('msg', '')}" for e in first
        )
        if len(errors) > len(first):
            summary += f" (+{len(errors) - len(first)} more)"
        body: dict[str, object] = {
            "type": "https://sfmapi.github.io/errors/validation",
            "title": "Validation error",
            "status": 422,
            "detail": summary or "Request body failed validation",
            "instance": str(request.url),
            "errors": errors,
        }
        return JSONResponse(
            status_code=422,
            content=body,
            media_type="application/problem+json",
        )

    app.include_router(health.router)
    from app.api.v1 import (
        admin,
        artifacts,
        backend,
        capabilities,
        datasets,
        images,
        jobs,
        localize,
        oneshot,
        pipelines,
        projects,
        reconstructions,
        resume,
        sfm_stages,
        similarity,
        uploads,
        ws_jobs,
    )

    app.include_router(projects.router, prefix="/v1")
    app.include_router(artifacts.router, prefix="/v1")
    app.include_router(backend.router, prefix="/v1")
    app.include_router(uploads.router, prefix="/v1")
    app.include_router(datasets.router, prefix="/v1")
    app.include_router(datasets.spherical_router, prefix="/v1")
    app.include_router(images.router, prefix="/v1")
    app.include_router(images.read_router, prefix="/v1")
    app.include_router(images.dataset_router, prefix="/v1")
    app.include_router(jobs.router, prefix="/v1")
    app.include_router(sfm_stages.router, prefix="/v1")
    app.include_router(reconstructions.router, prefix="/v1")
    app.include_router(pipelines.router, prefix="/v1")
    app.include_router(resume.router, prefix="/v1")
    app.include_router(admin.router, prefix="/v1")
    app.include_router(similarity.router, prefix="/v1")
    app.include_router(localize.router, prefix="/v1")
    app.include_router(capabilities.router, prefix="/v1")
    app.include_router(oneshot.router, prefix="/v1")
    app.include_router(ws_jobs.router)
    if mcp_app is not None:
        app.mount(mount_path, mcp_app)
    return app


app = create_app()
