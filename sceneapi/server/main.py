"""FastAPI app entrypoint.

Critical contract: importing this module must not import `pycolmap`,
`torch`, `cv2`, or any other heavy dep. Heavy deps live behind
`sceneapi.server.adapters.*` and are imported only inside worker processes.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute
from starlette.middleware.base import BaseHTTPMiddleware

from sceneapi.server import __version__
from sceneapi.server.api.v1 import health
from sceneapi.server.core.config import Settings, get_settings
from sceneapi.server.core.errors import SfmApiError
from sceneapi.server.core.ids import new_id
from sceneapi.server.core.logging import bind_request_context, configure_logging, get_logger
from sceneapi.server.core.profiling import RequestProfilingMiddleware
from sceneapi.server.core.public_outputs import sanitize_public_outputs

REQUEST_ID_HEADER = "X-Request-ID"

# OpenAPI extension stamped on every preview-tier operation so the
# conformance level (SPEC §1.3 [Preview]) is machine-visible whenever
# those operations are exposed via ``settings.expose_preview_apis``.
PREVIEW_CONFORMANCE_KEY = "x-sfmapi-conformance"
PREVIEW_CONFORMANCE_VALUE = "preview"


def _stamp_preview_conformance(*routers: APIRouter) -> None:
    """Mark every operation on the given routers as Preview tier.

    Mutates the module-level router objects before inclusion (FastAPI
    copies ``openapi_extra`` through ``include_router``); idempotent so
    repeated ``create_app()`` calls are safe.
    """
    for router in routers:
        for route in router.routes:
            if isinstance(route, APIRoute):
                extra = route.openapi_extra or {}
                extra.setdefault(PREVIEW_CONFORMANCE_KEY, PREVIEW_CONFORMANCE_VALUE)
                route.openapi_extra = extra


async def _janitor_loop(settings: Settings) -> None:
    """Background sweep that reclaims leases orphaned by dead workers.

    Runs in the web process (and inline-mode dev) on a
    ``janitor_interval_seconds`` cadence. Per-tick errors are swallowed so
    a transient DB hiccup doesn't kill the loop. Skipped in ephemeral mode
    — that's a single process, so a "dead worker" can't happen.
    """
    from sceneapi.server.db.session import get_session_factory
    from sceneapi.server.orchestrator.janitor import run_janitor_once

    log = get_logger("sceneapi.janitor")
    factory = get_session_factory()
    interval = max(1, settings.janitor_interval_seconds)
    while True:
        await asyncio.sleep(interval)
        try:
            async with factory() as session:
                await run_janitor_once(session)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("sceneapi.janitor_sweep_failed", error=str(exc))


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
        if "input" in item:
            item["input"] = sanitize_public_outputs(item["input"])
        errors.append(item)
    return errors


class RequestIdMiddleware(BaseHTTPMiddleware):
    """AIP-155 request correlation middleware.

    Reads the inbound ``X-Request-ID`` header (any non-empty value);
    falls back to a fresh ULID via :func:`sceneapi.server.core.ids.new_id` when
    absent. Echoes the resolved id in the response header so clients
    can stitch their logs to ours, and binds it (plus the resolved
    tenant_id when already on the request scope) onto
    :func:`structlog.contextvars` for the lifetime of the request via
    :func:`sceneapi.server.core.logging.bind_request_context`.

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
    log = get_logger("sceneapi.startup")
    log.info(
        "sceneapi.start",
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
        from sceneapi.server.db import models as _models  # noqa: F401 — register tables
        from sceneapi.server.db.base import Base
        from sceneapi.server.db.session import get_engine

        engine = get_engine(settings)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        # Ephemeral mode is for demos / embedded use / smoke tests
        # — register the no-op stub backend so workers can produce
        # terminal task statuses (most ops raise
        # CapabilityUnavailableError, which is fine for the
        # protocol-shape coverage these modes care about).
        import os

        from sceneapi.server.adapters.registry import register_backend
        from sceneapi.server.adapters.stub_backend import StubBackend

        register_backend("stub", StubBackend)
        if not (os.environ.get("SCENEAPI_BACKEND") or os.environ.get("SFMAPI_BACKEND")):
            os.environ["SCENEAPI_BACKEND"] = "stub"
        log.info("sceneapi.ephemeral_bootstrapped", workspace=str(settings.workspace_root))
    if settings.auto_load_backend_plugins:
        from sceneapi.server.adapters.registry import register_backend, register_backend_provider
        from sfm_hub.discovery import load_backend_entry_points

        loaded = load_backend_entry_points(
            register_backend,
            register_provider=register_backend_provider,
        )
        failures = [item for item in loaded if item.load_error]
        skipped = [item for item in loaded if item.skipped]
        registered = [item for item in loaded if not item.load_error and not item.skipped]
        log.info(
            "sceneapi.plugins_loaded",
            count=len(loaded),
            registered=len(registered),
            skipped=len(skipped),
            skipped_plugin_ids=[item.plugin_id for item in skipped],
            failures=len(failures),
        )
    if settings.warm_capabilities:
        try:
            from sceneapi.server.core.capabilities import detect_capabilities

            detect_capabilities()
            log.info("sceneapi.capabilities_warmed")
        except Exception as exc:
            log.warning("sceneapi.capabilities_warm_failed", error=str(exc))
    # Reclaim tasks orphaned by dead workers. Pointless in ephemeral mode
    # (single process — no separate worker to die), so skip it there.
    janitor_task: asyncio.Task[None] | None = None
    if not settings.ephemeral:
        janitor_task = asyncio.create_task(_janitor_loop(settings))
        log.info("sceneapi.janitor_started", interval=settings.janitor_interval_seconds)
    try:
        yield
    finally:
        if janitor_task is not None:
            janitor_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await janitor_task
        if settings.ephemeral:
            from sceneapi.server.db.session import _engine as _shared_engine
            from sceneapi.server.storage.blobs import reset_memory_blob_store_for_tests

            if _shared_engine is not None:
                await _shared_engine.dispose()
            reset_memory_blob_store_for_tests()
            import shutil

            shutil.rmtree(settings.workspace_root, ignore_errors=True)
            log.info("sceneapi.ephemeral_cleaned", workspace=str(settings.workspace_root))
        log.info("sceneapi.stop")


def create_app() -> FastAPI:
    settings = get_settings()
    app_lifespan = lifespan
    mcp_app = None
    if settings.mcp_api_enabled():
        from sceneapi.server.mcp.server import create_mcp_server

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
        title="SceneAPI",
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
    async def sceneapi_error_handler(request: Request, exc: SfmApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.as_problem(instance=request.url.path),
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
            "instance": request.url.path,
            "errors": errors,
        }
        return JSONResponse(
            status_code=422,
            content=body,
            media_type="application/problem+json",
        )

    app.include_router(health.router)
    from sceneapi.server.api.v1 import (
        admin,
        artifacts,
        backend,
        camera_models,
        capabilities,
        dataflow,
        dataset_stages,
        datasets,
        images,
        jobs,
        localize,
        oneshot,
        pipelines,
        projects,
        radiance,
        recon_stages,
        reconstructions,
        resume,
        sfm_stages,
        similarity,
        uploads,
        ws_jobs,
    )

    # Preview conformance tier (SPEC §1.3 [Preview]; lean audit D1/7.1).
    # These routers are ALWAYS mounted — requests keep serving exactly
    # as before — but they are fenced out of the OpenAPI document (the
    # default kernel contract) unless ``expose_preview_apis`` is set.
    # When exposed, every preview operation carries
    # ``x-sfmapi-conformance: preview``.
    preview_in_schema = settings.expose_preview_apis
    _stamp_preview_conformance(dataflow.router, admin.routing_router, similarity.router)

    app.include_router(projects.router, prefix="/v1")
    app.include_router(artifacts.router, prefix="/v1")
    app.include_router(backend.router, prefix="/v1")
    app.include_router(camera_models.router, prefix="/v1")
    app.include_router(uploads.router, prefix="/v1")
    app.include_router(datasets.router, prefix="/v1")
    app.include_router(datasets.spherical_router, prefix="/v1")
    app.include_router(images.router, prefix="/v1")
    app.include_router(images.read_router, prefix="/v1")
    app.include_router(images.dataset_router, prefix="/v1")
    app.include_router(jobs.router, prefix="/v1")
    app.include_router(sfm_stages.router, prefix="/v1")
    app.include_router(dataset_stages.router, prefix="/v1")
    app.include_router(reconstructions.router, prefix="/v1")
    app.include_router(recon_stages.router, prefix="/v1")
    app.include_router(pipelines.router, prefix="/v1")
    app.include_router(dataflow.router, prefix="/v1", include_in_schema=preview_in_schema)
    app.include_router(radiance.router, prefix="/v1")
    app.include_router(resume.router, prefix="/v1")
    app.include_router(admin.router, prefix="/v1")
    app.include_router(admin.routing_router, prefix="/v1", include_in_schema=preview_in_schema)
    app.include_router(similarity.router, prefix="/v1", include_in_schema=preview_in_schema)
    app.include_router(localize.router, prefix="/v1")
    app.include_router(capabilities.router, prefix="/v1")
    app.include_router(oneshot.router, prefix="/v1")
    app.include_router(ws_jobs.router)

    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        spec = get_openapi(
            title=app.title,
            version=app.version,
            routes=app.routes,
        )
        problem_ref = {"$ref": "#/components/schemas/ProblemResponse"}
        problem_content = {"application/problem+json": {"schema": problem_ref}}
        validation_ref = "#/components/schemas/HTTPValidationError"
        validation_content = {
            "application/problem+json": {"schema": problem_ref},
            "application/json": {"schema": {"$ref": validation_ref}},
        }
        common_problem_responses = {
            "400": "Bad request.",
            "401": "Authentication required.",
            "403": "Tenant boundary violation.",
            "404": "Resource not found.",
            "409": "Conflict.",
            "413": "Request body too large.",
            "429": "Quota exceeded.",
            "501": "Capability not available in this deployment.",
            "503": "Service unavailable.",
            "507": "Insufficient storage.",
        }
        for path_item in spec.get("paths", {}).values():
            for operation in path_item.values():
                if not isinstance(operation, dict):
                    continue
                responses = operation.get("responses", {})
                for code, response in responses.items():
                    if str(code).startswith("2"):
                        continue
                    content = response.get("content") or {}
                    json_schema = content.get("application/json", {}).get("schema")
                    if json_schema == {"$ref": validation_ref}:
                        response["content"] = validation_content
                    elif json_schema == problem_ref:
                        response["content"] = problem_content
                for code, description in common_problem_responses.items():
                    responses.setdefault(
                        code,
                        {
                            "description": description,
                            "content": problem_content,
                        },
                    )
        app.openapi_schema = spec
        return app.openapi_schema

    app.openapi = custom_openapi  # type: ignore[method-assign]
    if mcp_app is not None:
        app.mount(mount_path, mcp_app)
    return app


app = create_app()
