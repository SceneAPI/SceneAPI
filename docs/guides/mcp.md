# MCP adapter

sfmapi includes an optional FastMCP adapter for agent-facing access to
common read workflows. It is intentionally curated instead of generated
from the full OpenAPI document: agents get stable tools for discovery,
jobs, progress, projects, reconstructions, and sealed snapshots without
being exposed to every low-level REST operation.

## Plugin discovery tools

The MCP adapter exposes read-only plugin/runtime discovery:

- `list_plugins` and `get_plugin` inspect registered plugin manifests and
  installed/enabled state.
- `list_backend_providers` lists enabled provider ids and advertised
  capabilities.
- `doctor_plugin` runs the same diagnostics as `POST /v1/admin/plugins/{id}:doctor`.
- `plan_plugin_install` returns a dry-run install plan only. It never executes
  commands or records state, and provisioning output uses `env_keys`,
  `redacted_env`, and `outputs` rather than raw secret values.

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
uv run uvicorn sfmapi.runtime:create_app --factory --reload
```

The MCP endpoint is mounted at `/mcp` by default. The local HTML status
page is available at `/mcp/status`. Change the mount point with
`SFMAPI_MCP_MOUNT_PATH=/agent`.

`SFMAPI_MCP_ENABLED=true` is still accepted as a compatibility alias,
but new setups should prefer `SFMAPI_MCP_MODE=local`. When MCP is
mounted into the API process, `GET /v1/backend` includes `_links.mcp`
and `_links.mcp_status` so clients can discover the local adapter.

Backend packages can expose the same API-process MCP mount from their
own launchers. For example, with `sfmapi-colmap`:

```bash
uv run sfmapi-colmap-api \
  --backend colmap_cpp_native \
  --mcp local \
  --host 127.0.0.1 \
  --port 8000
```

Treat the API-process mount as a local or trusted-network convenience.
For desktop agents, prefer stdio because it is not reachable by other
processes over HTTP. Do not expose the MCP endpoint publicly without a
dedicated authorization layer.

## Use with Codex

Codex can connect to sfmapi over streamable HTTP. Start the API with
MCP mounted first, then register the endpoint:

```bash
uv run sfmapi serve --mcp local --host 127.0.0.1 --port 8000
codex mcp add sfmapi_colmap --url http://127.0.0.1:8000/mcp
codex mcp list
codex mcp get sfmapi_colmap
```

Use a simple underscore name such as `sfmapi_colmap`. It is easier to
quote across shells and avoids ambiguity in TOML dotted keys. The
persisted Codex config looks like:

```toml
[mcp_servers.sfmapi_colmap]
url = "http://127.0.0.1:8000/mcp"
```

Codex reads MCP configuration when a session starts. If you add the
server while Codex is already running, restart that Codex session before
expecting the tools to appear. For one-off non-interactive checks, pass
the same server as a runtime config override:

```bash
codex -c "mcp_servers.sfmapi_colmap.url='http://127.0.0.1:8000/mcp'" \
  exec "Use sfmapi_colmap to read sfmapi_version and sfmapi_capabilities."
```

Once connected, useful smoke-test tools are `sfmapi_version`,
`sfmapi_capabilities`, and `list_backend_actions` with
`include_schemas=true`.

## Use with Claude Code

Claude Code can connect to sfmapi's HTTP MCP endpoint directly. Start
the API with MCP mounted, then add the server using Claude Code's HTTP
MCP transport:

```bash
uv run sfmapi serve --mcp local --host 127.0.0.1 --port 8000
claude mcp add --transport http sfmapi_colmap http://127.0.0.1:8000/mcp
claude mcp list
claude mcp get sfmapi_colmap
```

Use `/mcp` inside Claude Code to inspect server status and available
tools. The default Claude Code scope is local to the current project
for the current user. To make the server available across your own
projects, add `--scope user`:

```bash
claude mcp add --transport http sfmapi_colmap \
  --scope user \
  http://127.0.0.1:8000/mcp
```

For a team-shared project config, use `--scope project` or create a
`.mcp.json` file in the project root:

```json
{
  "mcpServers": {
    "sfmapi_colmap": {
      "type": "http",
      "url": "http://127.0.0.1:8000/mcp"
    }
  }
}
```

Project-scoped MCP files are suitable only for non-secret local
endpoints or endpoints that use environment-expanded headers. Keep the
sfmapi MCP endpoint bound to loopback unless it is behind a dedicated
authorization layer.

See Claude Code's MCP documentation for the full set of scope,
authentication, and project-file options:
<https://docs.claude.com/en/docs/claude-code/mcp>.

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
- `list_artifacts`
- `get_artifact`
- `list_artifact_formats`
- `validate_artifact`
- `plan_artifact_conversion`
- `get_reconstruction`
- `list_submodels`
- `list_snapshots`

## Available resources

The same read-only data is also available through MCP resources for
clients that prefer resource reads over tool calls:

- `sfmapi://version`
- `sfmapi://capabilities`
- `sfmapi://artifacts/formats`
- `sfmapi://plugins`
- `sfmapi://plugins/{plugin_id}`
- `sfmapi://backend/actions`
- `sfmapi://backend/actions/{action_id}`
- `sfmapi://backend/providers`
- `sfmapi://tenants/{tenant_id}/projects`
- `sfmapi://tenants/{tenant_id}/jobs/{job_id}`
- `sfmapi://tenants/{tenant_id}/jobs/{job_id}/progress`
- `sfmapi://tenants/{tenant_id}/jobs/{job_id}/artifacts`
- `sfmapi://tenants/{tenant_id}/artifacts/{artifact_id}`
- `sfmapi://tenants/{tenant_id}/reconstructions/{recon_id}/artifacts`
- `sfmapi://tenants/{tenant_id}/reconstructions/{recon_id}/snapshots`

Backend action discovery is read-only. Mutation tools are not exposed
yet. Use the REST API or SDKs for project creation, uploads, pipeline
submission, backend action execution, cancellation, and resume until
those MCP actions have explicit safety and auth rules.

`list_backend_actions`, `get_backend_action`, and
`plan_artifact_conversion` accept an optional `provider` argument so
agents can inspect a specific installed backend provider without
changing `SFMAPI_BACKEND`.

Artifact tools expose metadata only. Use REST `GET
/v1/artifacts/{artifact_id}/content` or the SDKs for file transfer.
