"""FastMCP server entrypoint for sfmapi."""

from __future__ import annotations

import argparse
import contextlib
import functools
import inspect
import ipaddress
import os
import sys
from collections.abc import Iterator, Sequence
from typing import Any

from starlette.responses import HTMLResponse, JSONResponse

from app import __version__
from app.core.config import get_settings
from app.mcp.tools import TOOLS

READ_ONLY_TOOL_ANNOTATIONS: dict[str, bool] = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}
READ_ONLY_RESOURCE_ANNOTATIONS: dict[str, bool] = {
    "readOnlyHint": True,
    "idempotentHint": True,
}

TOOL_TITLES: dict[str, str] = {
    "sfmapi_version": "Read sfmapi version",
    "sfmapi_capabilities": "Read sfmapi capabilities",
    "list_backend_actions": "List backend actions",
    "get_backend_action": "Read backend action",
    "list_plugins": "List backend plugins",
    "get_plugin": "Read backend plugin",
    "doctor_plugin": "Diagnose backend plugin",
    "list_backend_providers": "List backend providers",
    "plan_plugin_install": "Plan plugin install",
    "list_projects": "List sfmapi projects",
    "list_jobs": "List sfmapi jobs",
    "get_job": "Read an sfmapi job",
    "get_job_progress": "Read sfmapi job progress",
    "list_artifacts": "List sfmapi artifacts",
    "get_artifact": "Read an sfmapi artifact",
    "list_artifact_formats": "List artifact formats",
    "validate_artifact": "Validate an artifact",
    "plan_artifact_conversion": "Plan artifact conversion",
    "get_reconstruction": "Read an sfmapi reconstruction",
    "list_submodels": "List sfmapi submodels",
    "list_snapshots": "List sfmapi snapshots",
}

MCP_INSTRUCTIONS = """Read-only local adapter for sfmapi.

Use these tools to inspect sfmapi server state, capabilities, backend
plugins, providers, action schemas, jobs, progress, artifact formats,
conversion plans, reconstructions, submodels, and sealed snapshot metadata. It also lists
typed stage artifacts so agents can
inspect selected feature, match, verification, and snapshot outputs
without scraping task payloads. The adapter does not create projects, upload images,
submit pipelines, install plugins, run backend actions, cancel work, resume work, or
serve binary snapshot contents. Plugin install planning is dry-run only.
Use the REST API or SDKs for mutations and bulk data transfer.
"""


def _tool_names() -> list[str]:
    return [tool.__name__ for tool in TOOLS]


def _resource_names() -> list[str]:
    return [
        "sfmapi://version",
        "sfmapi://capabilities",
        "sfmapi://artifacts/formats",
        "sfmapi://plugins",
        "sfmapi://plugins/{plugin_id}",
        "sfmapi://backend/providers",
        "sfmapi://backend/actions",
        "sfmapi://backend/actions/{action_id}",
        "sfmapi://tenants/{tenant_id}/projects",
        "sfmapi://tenants/{tenant_id}/jobs/{job_id}",
        "sfmapi://tenants/{tenant_id}/jobs/{job_id}/progress",
        "sfmapi://tenants/{tenant_id}/jobs/{job_id}/artifacts",
        "sfmapi://tenants/{tenant_id}/artifacts/{artifact_id}",
        "sfmapi://tenants/{tenant_id}/reconstructions/{recon_id}/artifacts",
        "sfmapi://tenants/{tenant_id}/reconstructions/{recon_id}/snapshots",
    ]


def _html_status(endpoint_hint: str) -> str:
    tools = "\n".join(f"<li><code>{name}</code></li>" for name in _tool_names())
    resources = "\n".join(f"<li><code>{name}</code></li>" for name in _resource_names())
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>sfmapi MCP</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 2rem; line-height: 1.5; max-width: 760px; }}
    code {{ background: #f3f4f6; border-radius: 4px; padding: 0.1rem 0.3rem; }}
  </style>
</head>
<body>
  <h1>sfmapi MCP</h1>
  <p>Local FastMCP adapter for sfmapi. MCP clients should connect to <code>{endpoint_hint}</code>.</p>
  <h2>Tools</h2>
  <ul>{tools}</ul>
  <h2>Resources</h2>
  <ul>{resources}</ul>
</body>
</html>"""


def _is_loopback_host(host: str) -> bool:
    if host.lower() in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@contextlib.contextmanager
def _startup_stdout_to_stderr() -> Iterator[None]:
    try:
        stdout_fd = sys.stdout.fileno()
        stderr_fd = sys.stderr.fileno()
    except (AttributeError, OSError, ValueError):
        with contextlib.redirect_stdout(sys.stderr):
            yield
        return

    sys.stdout.flush()
    sys.stderr.flush()
    saved_stdout_fd = os.dup(stdout_fd)
    try:
        os.dup2(stderr_fd, stdout_fd)
        with contextlib.redirect_stdout(sys.stderr):
            yield
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        os.dup2(saved_stdout_fd, stdout_fd)
        os.close(saved_stdout_fd)


def _load_backend_plugins_for_standalone(*, stdio: bool = False) -> None:
    settings = get_settings()
    if not settings.auto_load_backend_plugins:
        return

    from app.adapters.registry import register_backend, register_backend_provider
    from sfm_hub.discovery import load_backend_entry_points

    context = _startup_stdout_to_stderr() if stdio else contextlib.nullcontext()
    with context:
        load_backend_entry_points(
            register_backend,
            register_provider=register_backend_provider,
        )


def _warm_stdio_backend_runtime() -> None:
    if not os.environ.get("SFMAPI_BACKEND"):
        return

    from app.adapters.registry import get_backend

    with _startup_stdout_to_stderr():
        try:
            get_backend().runtime_versions()
        except KeyError:
            return
        except Exception as exc:
            print(f"sfmapi.mcp.stdio_backend_warm_failed: {exc}", file=sys.stderr)


@contextlib.contextmanager
def _call_stdout_to_stderr() -> Iterator[None]:
    with contextlib.redirect_stdout(sys.stderr):
        yield


def _stdio_safe_callable(func: Any) -> Any:
    signature = inspect.signature(func)

    if inspect.iscoroutinefunction(func):

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            with _call_stdout_to_stderr():
                return await func(*args, **kwargs)

        async_wrapper.__signature__ = signature  # type: ignore[attr-defined]
        return async_wrapper

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        with _call_stdout_to_stderr():
            return func(*args, **kwargs)

    wrapper.__signature__ = signature  # type: ignore[attr-defined]
    return wrapper


def create_mcp_server(
    *,
    name: str = "sfmapi",
    include_index_route: bool = True,
    endpoint_hint: str = "/mcp",
    stdio_safe_output: bool = False,
) -> Any:
    """Create the FastMCP server without importing FastMCP at module import time."""
    from app.mcp import tools as tool_impl

    tool_impl.validate_configuration()
    try:
        from fastmcp import FastMCP  # type: ignore[import-not-found,unused-ignore]
    except ModuleNotFoundError as exc:  # pragma: no cover - exercised only without the extra
        raise RuntimeError(
            "FastMCP is not installed. Install the MCP extra with "
            "`uv sync --extra mcp` or `pip install 'sfmapi[mcp]'`."
        ) from exc

    mcp = FastMCP(
        name,
        instructions=MCP_INSTRUCTIONS,
        version=__version__,
        website_url="https://sfmapi.github.io",
        strict_input_validation=False,
    )

    def registerable(func: Any) -> Any:
        return _stdio_safe_callable(func) if stdio_safe_output else func

    for tool in TOOLS:
        mcp.tool(
            registerable(tool),
            title=TOOL_TITLES.get(tool.__name__),
            annotations=READ_ONLY_TOOL_ANNOTATIONS,
            tags={"sfmapi", "read"},
            meta={"sfmapi": {"surface": "mcp", "access": "read-only"}},
        )

    mcp.resource(
        "sfmapi://version",
        title="sfmapi version",
        mime_type="application/json",
        annotations=READ_ONLY_RESOURCE_ANNOTATIONS,
        tags={"sfmapi", "discovery"},
    )(registerable(tool_impl.sfmapi_version))
    mcp.resource(
        "sfmapi://capabilities",
        title="sfmapi capabilities",
        mime_type="application/json",
        annotations=READ_ONLY_RESOURCE_ANNOTATIONS,
        tags={"sfmapi", "discovery"},
    )(registerable(tool_impl.sfmapi_capabilities))
    mcp.resource(
        "sfmapi://artifacts/formats",
        title="sfmapi artifact formats",
        mime_type="application/json",
        annotations=READ_ONLY_RESOURCE_ANNOTATIONS,
        tags={"sfmapi", "artifacts"},
    )(registerable(tool_impl.list_artifact_formats))

    async def plugins_resource() -> dict[str, Any]:
        return await tool_impl.list_plugins()

    mcp.resource(
        "sfmapi://plugins",
        title="sfmapi backend plugins",
        mime_type="application/json",
        annotations=READ_ONLY_RESOURCE_ANNOTATIONS,
        tags={"sfmapi", "plugins", "discovery"},
    )(registerable(plugins_resource))
    mcp.resource(
        "sfmapi://plugins/{plugin_id}",
        title="sfmapi backend plugin",
        mime_type="application/json",
        annotations=READ_ONLY_RESOURCE_ANNOTATIONS,
        tags={"sfmapi", "plugins", "discovery"},
    )(registerable(tool_impl.get_plugin))

    async def backend_providers_resource() -> dict[str, Any]:
        return await tool_impl.list_backend_providers()

    mcp.resource(
        "sfmapi://backend/providers",
        title="sfmapi backend providers",
        mime_type="application/json",
        annotations=READ_ONLY_RESOURCE_ANNOTATIONS,
        tags={"sfmapi", "backend", "plugins", "discovery"},
    )(registerable(backend_providers_resource))

    async def backend_actions_resource() -> dict[str, Any]:
        return await tool_impl.list_backend_actions()

    mcp.resource(
        "sfmapi://backend/actions",
        title="sfmapi backend actions",
        mime_type="application/json",
        annotations=READ_ONLY_RESOURCE_ANNOTATIONS,
        tags={"sfmapi", "backend", "discovery"},
    )(registerable(backend_actions_resource))
    mcp.resource(
        "sfmapi://backend/actions/{action_id}",
        title="sfmapi backend action",
        mime_type="application/json",
        annotations=READ_ONLY_RESOURCE_ANNOTATIONS,
        tags={"sfmapi", "backend", "discovery"},
    )(registerable(tool_impl.get_backend_action))
    mcp.resource(
        "sfmapi://tenants/{tenant_id}/projects",
        title="sfmapi tenant projects",
        mime_type="application/json",
        annotations=READ_ONLY_RESOURCE_ANNOTATIONS,
        tags={"sfmapi", "projects"},
    )(registerable(tool_impl.list_projects))
    mcp.resource(
        "sfmapi://tenants/{tenant_id}/jobs/{job_id}",
        title="sfmapi job",
        mime_type="application/json",
        annotations=READ_ONLY_RESOURCE_ANNOTATIONS,
        tags={"sfmapi", "jobs"},
    )(registerable(tool_impl.get_job))
    mcp.resource(
        "sfmapi://tenants/{tenant_id}/jobs/{job_id}/progress",
        title="sfmapi job progress",
        mime_type="application/json",
        annotations=READ_ONLY_RESOURCE_ANNOTATIONS,
        tags={"sfmapi", "jobs"},
    )(registerable(tool_impl.get_job_progress))

    async def job_artifacts_resource(tenant_id: str, job_id: str) -> dict[str, Any]:
        return await tool_impl.list_artifacts(tenant_id=tenant_id, job_id=job_id)

    async def reconstruction_artifacts_resource(tenant_id: str, recon_id: str) -> dict[str, Any]:
        return await tool_impl.list_artifacts(tenant_id=tenant_id, recon_id=recon_id)

    mcp.resource(
        "sfmapi://tenants/{tenant_id}/jobs/{job_id}/artifacts",
        title="sfmapi job artifacts",
        mime_type="application/json",
        annotations=READ_ONLY_RESOURCE_ANNOTATIONS,
        tags={"sfmapi", "jobs", "artifacts"},
    )(registerable(job_artifacts_resource))
    mcp.resource(
        "sfmapi://tenants/{tenant_id}/artifacts/{artifact_id}",
        title="sfmapi artifact",
        mime_type="application/json",
        annotations=READ_ONLY_RESOURCE_ANNOTATIONS,
        tags={"sfmapi", "artifacts"},
    )(registerable(tool_impl.get_artifact))
    mcp.resource(
        "sfmapi://tenants/{tenant_id}/reconstructions/{recon_id}/artifacts",
        title="sfmapi reconstruction artifacts",
        mime_type="application/json",
        annotations=READ_ONLY_RESOURCE_ANNOTATIONS,
        tags={"sfmapi", "reconstructions", "artifacts"},
    )(registerable(reconstruction_artifacts_resource))
    mcp.resource(
        "sfmapi://tenants/{tenant_id}/reconstructions/{recon_id}/snapshots",
        title="sfmapi reconstruction snapshots",
        mime_type="application/json",
        annotations=READ_ONLY_RESOURCE_ANNOTATIONS,
        tags={"sfmapi", "reconstructions"},
    )(registerable(tool_impl.list_snapshots))

    async def healthz(request: Any) -> JSONResponse:
        return JSONResponse({"status": "ok", "service": "sfmapi-mcp"})

    async def status(request: Any) -> HTMLResponse:
        return HTMLResponse(_html_status(endpoint_hint))

    mcp.custom_route("/healthz", methods=["GET"])(healthz)
    mcp.custom_route("/status", methods=["GET"])(status)

    if include_index_route:

        async def index(request: Any) -> HTMLResponse:
            return HTMLResponse(_html_status(endpoint_hint))

        mcp.custom_route("/", methods=["GET"])(index)

    return mcp


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the sfmapi FastMCP server.")
    parser.add_argument(
        "--transport",
        choices=("stdio", "http"),
        default=None,
        help=("MCP transport. Defaults to SFMAPI_MCP_MODE when it is stdio/http, otherwise stdio."),
    )
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind host.")
    parser.add_argument("--port", type=int, default=9000, help="HTTP bind port.")
    parser.add_argument("--path", default="/mcp", help="HTTP MCP endpoint path.")
    parser.add_argument(
        "--allow-non-loopback",
        action="store_true",
        help="Allow HTTP binding to a non-loopback host. Use only behind trusted network controls.",
    )
    args = parser.parse_args(argv)
    settings = get_settings()
    transport = args.transport
    if transport is None:
        transport = "http" if settings.mcp_mode == "http" else "stdio"

    if transport == "http" and not args.allow_non_loopback and not _is_loopback_host(args.host):
        parser.error(
            "HTTP transport defaults to local-only use. Bind to 127.0.0.1, "
            "or pass --allow-non-loopback when a trusted proxy/network layer protects it."
        )
    _load_backend_plugins_for_standalone(stdio=transport == "stdio")
    if transport == "stdio":
        _warm_stdio_backend_runtime()
    mcp = create_mcp_server(
        endpoint_hint=args.path,
        stdio_safe_output=transport == "stdio",
    )
    if transport == "stdio":
        mcp.run(transport="stdio")
        return
    mcp.run(transport="http", host=args.host, port=args.port, path=args.path)


if __name__ == "__main__":
    main()
