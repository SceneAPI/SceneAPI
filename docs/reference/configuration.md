# Configuration

All settings are env-vars prefixed with `SFMAPI_`. They're parsed by a
single Pydantic `Settings` class:

```{eval-rst}
.. autoclass:: app.core.config.Settings
   :members:
   :no-index:
```

## Common bundles

### Single-host dev (default)

```bash
SFMAPI_ENV=dev
SFMAPI_DB_URL=sqlite+aiosqlite:///./sfmapi.db
SFMAPI_AUTH_MODE=none
SFMAPI_INLINE_TASKS=false
```

### Production (web tier in docker compose)

```bash
SFMAPI_ENV=prod
SFMAPI_DB_URL=postgresql+psycopg://sfm:secret@postgres:5432/sfmapi
SFMAPI_QUEUE_BACKEND=arq
SFMAPI_REDIS_URL=redis://redis:6379/0
SFMAPI_WORKSPACE_ROOT=/workspaces
SFMAPI_BLOB_BACKEND=fs
SFMAPI_AUTH_MODE=api_key
SFMAPI_LOG_LEVEL=INFO
```

### Worker (Windows + CUDA)

```bash
SFMAPI_DB_URL=postgresql+psycopg://sfm:secret@db.internal:5432/sfmapi
SFMAPI_REDIS_URL=redis://redis.internal:6379/0
SFMAPI_QUEUE_BACKEND=arq
SFMAPI_BACKEND=<registered-backend-name>
SFMAPI_RUNTIME_VERSION_ID=<backend-runtime-fingerprint>
SFMAPI_LEASE_TTL_SECONDS=30
SFMAPI_INLINE_TASKS=false
CUDA_VISIBLE_DEVICES=0
```

Backend packages may define their own engine, CUDA, model, or runtime
environment variables. sfmapi selects the registered backend name and
uses `SFMAPI_RUNTIME_VERSION_ID` as an extra cache-key salt; backend
packages usually compute this value from their own engine, CUDA, and
build metadata.

## Notable knobs

| Env var | Default | What it does |
|---|---|---|
| `SFMAPI_INLINE_TASKS` | false | Run tasks in-process (test mode) |
| `SFMAPI_LEASE_TTL_SECONDS` | 30 | Per-task lease TTL |
| `SFMAPI_JANITOR_INTERVAL_SECONDS` | 10 | Reclaim expired leases |
| `SFMAPI_SNAPSHOT_KEEP_LAST` | 3 | GC keeps last N + final |
| `SFMAPI_UPLOAD_CHUNK_MAX_BYTES` | 8 MiB | Max single PATCH chunk |
| `SFMAPI_UPLOAD_EXPIRY_HOURS` | 24 | Open uploads GC'd after this |
| `SFMAPI_BACKEND` | unset | Registered backend name to select at startup |
| `SFMAPI_RUNTIME_VERSION_ID` | `unknown` | Extra cache-key salt exposed in `/v1/version` |
| `SFMAPI_PROFILE_REQUESTS` | false | Enable per-request cProfile instrumentation |
| `SFMAPI_PROFILE_MIN_MS` | 0 | Only log profiles for requests at/above this duration |
| `SFMAPI_PROFILE_TOP_N` | 20 | Number of profiler rows included in each profile log |
| `SFMAPI_PROFILE_SORT_BY` | `cumulative` | pstats sort key: `cumulative`, `tottime`, `time`, or `calls` |
| `SFMAPI_PROFILE_DIR` | unset | Optional directory for raw `.prof` request dumps |
| `SFMAPI_WARM_CAPABILITIES` | false | Probe and cache `/v1/capabilities` during startup |
| `SFMAPI_MCP_MODE` | `off` | MCP mode: `off`, `local`, `stdio`, or `http`; `local` mounts MCP into the API process |
| `SFMAPI_MCP_ENABLED` | false | Backward-compatible alias for mounting the optional FastMCP adapter into the API process |
| `SFMAPI_MCP_MOUNT_PATH` | `/mcp` | Mount path for the MCP endpoint and status routes |
| `SFMAPI_MCP_TENANT_ID` | unset | Required MCP tenant scope when `SFMAPI_AUTH_MODE=api_key` |

## Request profiling

Enable request profiling only during diagnosis:

```bash
SFMAPI_PROFILE_REQUESTS=true \
SFMAPI_PROFILE_MIN_MS=100 \
SFMAPI_PROFILE_DIR=./profiles \
uv run uvicorn app.main:app
```

Profiled responses include a `Server-Timing: app;dur=<ms>` header.
Requests at or above `SFMAPI_PROFILE_MIN_MS` emit a structured
`request.profiled` log with the top functions from `cProfile`; when
`SFMAPI_PROFILE_DIR` is set, the same threshold controls raw `.prof`
dumps. Inspect dumps with `python -m pstats ./profiles/<file>.prof`.
