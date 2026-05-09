"""Optional request profiling middleware."""

from __future__ import annotations

import cProfile
import hashlib
import io
import pstats
import time
from pathlib import Path

from fastapi import Request
from fastapi.responses import Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.core.config import get_settings
from app.core.logging import get_logger


class RequestProfilingMiddleware(BaseHTTPMiddleware):
    """Profile requests when ``SFMAPI_PROFILE_REQUESTS=true``.

    The middleware is intentionally dormant by default. When enabled,
    it records cProfile stats around the request handler, adds a compact
    ``Server-Timing`` header, logs top cumulative functions for slow
    requests, and can write raw ``.prof`` files for tools such as
    SnakeViz or ``python -m pstats``.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        settings = get_settings()
        if not settings.profile_requests:
            return await call_next(request)

        started = time.perf_counter()
        profiler = cProfile.Profile()
        response: Response | None = None
        error: BaseException | None = None
        profiler.enable()
        try:
            response = await call_next(request)
            return response
        except BaseException as exc:
            error = exc
            raise
        finally:
            profiler.disable()
            duration_ms = (time.perf_counter() - started) * 1000.0
            if response is not None:
                response.headers["Server-Timing"] = f"app;dur={duration_ms:.2f}"
            self._record_profile(
                request=request,
                response=response,
                profiler=profiler,
                duration_ms=duration_ms,
                error=error,
            )

    def _record_profile(
        self,
        *,
        request: Request,
        response: Response | None,
        profiler: cProfile.Profile,
        duration_ms: float,
        error: BaseException | None,
    ) -> None:
        settings = get_settings()
        should_report = duration_ms >= settings.profile_min_ms or error is not None
        if not should_report:
            return

        profile_path = self._dump_profile(
            profiler=profiler,
            directory=settings.profile_dir,
            request=request,
            duration_ms=duration_ms,
        )

        stream = io.StringIO()
        pstats.Stats(profiler, stream=stream).strip_dirs().sort_stats(
            settings.profile_sort_by
        ).print_stats(max(1, settings.profile_top_n))

        route = request.scope.get("route")
        route_path = getattr(route, "path", None)
        get_logger("sfmapi.profile").info(
            "request.profiled",
            method=request.method,
            path=request.url.path,
            route=route_path,
            status_code=getattr(response, "status_code", None),
            duration_ms=round(duration_ms, 2),
            profile_sort_by=settings.profile_sort_by,
            profile_top_n=settings.profile_top_n,
            profile_path=str(profile_path) if profile_path else None,
            error=type(error).__name__ if error else None,
            profile=stream.getvalue(),
        )

    def _dump_profile(
        self,
        *,
        profiler: cProfile.Profile,
        directory: Path | None,
        request: Request,
        duration_ms: float,
    ) -> Path | None:
        if directory is None:
            return None
        directory.mkdir(parents=True, exist_ok=True)
        route = request.scope.get("route")
        route_path = getattr(route, "path", request.url.path)
        digest = hashlib.sha1(f"{request.method} {request.url.path}".encode()).hexdigest()[:10]
        slug = (
            str(route_path)
            .strip("/")
            .replace("/", "_")
            .replace("{", "")
            .replace("}", "")
            .replace(":", "_")
            or "root"
        )
        filename = (
            f"{time.time_ns()}_{request.method.lower()}_{slug}_{int(duration_ms)}ms_{digest}.prof"
        )
        path = directory / filename
        profiler.dump_stats(str(path))
        return path


__all__ = ["RequestProfilingMiddleware"]
