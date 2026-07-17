# Configuration

This page covers **server configuration** — the `SCENEAPI_*` environment
variables that an operator sets on the process. If you're looking for how to
**configure a job** (request fields, `backend_options`, config-schemas,
canonical knob names), see {doc}`job_configuration` instead.

All settings are env-vars prefixed with `SCENEAPI_`; the pre-rename
`SFMAPI_*` spellings are honored via a deprecation alias for one
release (removed in 0.2.0). They're parsed by a single Pydantic
`Settings` class:

```{eval-rst}
.. autoclass:: sceneapi.server.core.config.Settings
   :members:
   :no-index:
```

## Common bundles

### Single-host dev (default)

```bash
SCENEAPI_ENV=dev
SCENEAPI_DB_URL=sqlite+aiosqlite:///./sfmapi.db
SCENEAPI_AUTH_MODE=none
SCENEAPI_INLINE_TASKS=false
```

### Production (web tier in docker compose)

```bash
SCENEAPI_ENV=prod
SCENEAPI_DB_URL=postgresql+psycopg://sfm:secret@postgres:5432/sfmapi
SCENEAPI_QUEUE_BACKEND=arq
SCENEAPI_REDIS_URL=redis://redis:6379/0
SCENEAPI_WORKSPACE_ROOT=/workspaces
SCENEAPI_BLOB_BACKEND=fs
SCENEAPI_AUTH_MODE=api_key
SCENEAPI_LOG_LEVEL=INFO
```

### Worker (Windows + CUDA)

```bash
SCENEAPI_DB_URL=postgresql+psycopg://sfm:secret@db.internal:5432/sfmapi
SCENEAPI_REDIS_URL=redis://redis.internal:6379/0
SCENEAPI_QUEUE_BACKEND=arq
SCENEAPI_BACKEND=<registered-backend-name>
SCENEAPI_RUNTIME_VERSION_ID=<backend-runtime-fingerprint>
SCENEAPI_LEASE_TTL_SECONDS=30
SCENEAPI_INLINE_TASKS=false
CUDA_VISIBLE_DEVICES=0
```

Backend packages may define their own engine, CUDA, model, or runtime
environment variables. sfmapi selects the registered backend name and
uses `SCENEAPI_RUNTIME_VERSION_ID` as an extra cache-key salt; backend
packages usually compute this value from their own engine, CUDA, and
build metadata.

When backend plugins are installed through `sceneapi plugins`, local hub
state is stored in `~/.config/sfmapi/plugins.json` by default. Set
`SCENEAPI_PLUGIN_STATE` to a shared path when several web or worker
processes must agree on enabled plugins and routing profiles.

`SCENEAPI_AUTO_LOAD_BACKEND_PLUGINS` is `true` by default — every
installed `[project.entry-points."sceneapi.backends"]` is registered at
lifespan startup, matching the standard Python plugin-ecosystem
expectation (`pip install sfmapi_colmap_cli` activates the plugin
without a separate opt-in). Set it to `false` for deployments that
want an explicit allowlist; tests already pin it `false` to keep their
registry deterministic.

## Notable knobs

| Env var | Default | What it does |
|---|---|---|
| `SCENEAPI_INLINE_TASKS` | false | Run tasks in-process (test mode) |
| `SCENEAPI_LEASE_TTL_SECONDS` | 30 | Per-task lease TTL |
| `SCENEAPI_JANITOR_INTERVAL_SECONDS` | 10 | Reclaim expired leases |
| `SCENEAPI_SNAPSHOT_KEEP_LAST` | 3 | GC keeps last N + final |
| `SCENEAPI_UPLOAD_CHUNK_MAX_BYTES` | 8 MiB | Max single PATCH chunk |
| `SCENEAPI_UPLOAD_EXPIRY_HOURS` | 24 | Open uploads GC'd after this |
| `SCENEAPI_ARCHIVE_IMPORT_MAX_BYTES` | 5 GiB | `datasets:fromArchive` uncompressed image cap (0 disables; checked from the zip central directory before decompression) |
| `SCENEAPI_BACKEND` | unset | Registered backend name to select at startup |
| `SCENEAPI_AUTO_LOAD_BACKEND_PLUGINS` | true | Load installed backend plugin entry points during API startup (set false for explicit-allowlist deployments) |
| `SCENEAPI_PLUGIN_STATE` | `~/.config/sfmapi/plugins.json` | Local plugin enablement and routing-profile state file |
| `SCENEAPI_RUNTIME_VERSION_ID` | `unknown` | Extra cache-key salt exposed in `/v1/version` |
| `SCENEAPI_PROFILE_REQUESTS` | false | Enable per-request cProfile instrumentation |
| `SCENEAPI_PROFILE_MIN_MS` | 0 | Only log profiles for requests at/above this duration |
| `SCENEAPI_PROFILE_TOP_N` | 20 | Number of profiler rows included in each profile log |
| `SCENEAPI_PROFILE_SORT_BY` | `cumulative` | pstats sort key: `cumulative`, `tottime`, `time`, or `calls` |
| `SCENEAPI_PROFILE_DIR` | unset | Optional directory for raw `.prof` request dumps |
| `SCENEAPI_WARM_CAPABILITIES` | false | Probe and cache `/v1/capabilities` during startup |
| `SCENEAPI_MCP_MODE` | `off` | MCP mode: `off`, `local`, `stdio`, or `http`; `local` mounts MCP into the API process |
| `SCENEAPI_MCP_ENABLED` | false | Backward-compatible alias for mounting the optional FastMCP adapter into the API process |
| `SCENEAPI_MCP_MOUNT_PATH` | `/mcp` | Mount path for the MCP endpoint and status routes |
| `SCENEAPI_MCP_TENANT_ID` | unset | Required MCP tenant scope when `SCENEAPI_AUTH_MODE=api_key` |

## Request profiling

Enable request profiling only during diagnosis:

```bash
SCENEAPI_PROFILE_REQUESTS=true \
SCENEAPI_PROFILE_MIN_MS=100 \
SCENEAPI_PROFILE_DIR=./profiles \
uv run uvicorn sceneapi.runtime:create_app --factory
```

Profiled responses include a `Server-Timing: app;dur=<ms>` header.
Requests at or above `SCENEAPI_PROFILE_MIN_MS` emit a structured
`request.profiled` log with the top functions from `cProfile`; when
`SCENEAPI_PROFILE_DIR` is set, the same threshold controls raw `.prof`
dumps. Inspect dumps with `python -m pstats ./profiles/<file>.prof`.
