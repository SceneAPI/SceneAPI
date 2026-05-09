# MCP adapter

sfmapi includes an optional FastMCP adapter for agent-facing access to
common read workflows. It is intentionally curated instead of generated
from the full OpenAPI document: agents get stable tools for discovery,
jobs, progress, projects, reconstructions, and sealed snapshots without
being exposed to every low-level REST operation.

## Install

Install the optional dependency group:

```bash
uv sync --extra mcp
```

The extra pins FastMCP to the current v3 release used by these docs.
Review and update that pin deliberately when upgrading MCP behavior.

For development, include both extras:

```bash
uv sync --extra dev --extra mcp
```

## Run locally over stdio

Use stdio for desktop agents that launch local MCP servers:

```bash
uv run sfmapi mcp
```

The legacy direct entrypoint is equivalent:

```bash
uv run sfmapi-mcp
```

This uses the same `SFMAPI_*` environment variables as the REST server,
including `SFMAPI_DB_URL`, `SFMAPI_DEFAULT_TENANT`, and backend
registration settings. When `SFMAPI_AUTH_MODE=api_key`, set
`SFMAPI_MCP_TENANT_ID` so MCP reads are scoped to exactly one tenant.

## Run locally over HTTP

Use HTTP when you want an MCP endpoint reachable by local tools or a
browser-based inspector:

```bash
uv run sfmapi mcp --transport http --host 127.0.0.1 --port 9000
```

The MCP endpoint is `http://127.0.0.1:9000/mcp`. A simple local HTML
status page is served at `http://127.0.0.1:9000/`, with JSON health at
`http://127.0.0.1:9000/healthz`.

`uv run sfmapi-mcp --transport http --host 127.0.0.1 --port 9000` is
kept as a direct entrypoint for existing agent launchers.

The standalone HTTP command rejects non-loopback hosts by default. If
you intentionally bind outside localhost, put it behind trusted network
controls and opt in explicitly:

```bash
uv run sfmapi mcp --transport http --host 0.0.0.0 --allow-non-loopback
```

## Serve from the API process

To mount MCP into the FastAPI application:

```bash
uv run sfmapi serve --mcp local --reload
```

On PowerShell:

```powershell
$env:SFMAPI_MCP_MODE = "local"
uv run uvicorn app.main:app --reload
```

The MCP endpoint is mounted at `/mcp` by default. The local HTML status
page is available at `/mcp/status`. Change the mount point with
`SFMAPI_MCP_MOUNT_PATH=/agent`.

`SFMAPI_MCP_ENABLED=true` is still accepted as a compatibility alias,
but new setups should prefer `SFMAPI_MCP_MODE=local`. When MCP is
mounted into the API process, `GET /v1/backend` includes `_links.mcp`
and `_links.mcp_status` so clients can discover the local adapter.

Treat the API-process mount as a local or trusted-network convenience.
For desktop agents, prefer stdio because it is not reachable by other
processes over HTTP. Do not expose the MCP endpoint publicly without a
dedicated authorization layer.

## Tenant scope

MCP does not reuse FastAPI request dependencies, so it does not read
`Authorization` headers from the REST API. In `auth_mode=none`, tools
default to `SFMAPI_DEFAULT_TENANT`. In `auth_mode=api_key`, set
`SFMAPI_MCP_TENANT_ID=<tenant>`; calls that omit the tenant or pass that
same tenant are allowed, and calls for any other tenant fail.

## Available tools

Every tool is annotated as read-only, non-destructive, idempotent, and
closed-world. The annotations are hints for MCP clients; authorization
and network boundaries must still be enforced by deployment.

- `sfmapi_version`
- `sfmapi_capabilities`
- `list_backend_actions`
- `get_backend_action`
- `list_projects`
- `list_jobs`
- `get_job`
- `get_job_progress`
- `get_reconstruction`
- `list_submodels`
- `list_snapshots`

## Available resources

The same read-only data is also available through MCP resources for
clients that prefer resource reads over tool calls:

- `sfmapi://version`
- `sfmapi://capabilities`
- `sfmapi://backend/actions`
- `sfmapi://backend/actions/{action_id}`
- `sfmapi://tenants/{tenant_id}/projects`
- `sfmapi://tenants/{tenant_id}/jobs/{job_id}`
- `sfmapi://tenants/{tenant_id}/jobs/{job_id}/progress`
- `sfmapi://tenants/{tenant_id}/reconstructions/{recon_id}/snapshots`

Backend action discovery is read-only. Mutation tools are not exposed
yet. Use the REST API or SDKs for project creation, uploads, pipeline
submission, backend action execution, cancellation, and resume until
those MCP actions have explicit safety and auth rules.
