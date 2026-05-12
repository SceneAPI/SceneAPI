# sfmapi

**A generic HTTP/REST API for Structure-from-Motion tasks.** Backend-
agnostic by design: any SfM engine that conforms to the spec can serve
it (pycolmap, OpenSfM, hloc, custom forks). Sealed-snapshot progress,
content-addressed storage, multi-tenant from day 1.

This repository ships the **wire spec + orchestration shell only** —
no concrete SfM engine. Backend implementations live in their own
repositories, satisfy the smallest applicable protocol in
`app.adapters.backend`, and register at startup via
`register_backend("name", Backend)`. A no-op
`StubBackend` is bundled for tests and `SFMAPI_EPHEMERAL=true` demos.

Client SDKs now live in the sibling `sfmapi-sdk` repository. This repo owns
the server, OpenAPI contract, plugin hub, and backend interfaces; the SDK repo
packages the Python, TypeScript, and C++ clients from that contract.

Reference backend packages use the same discovery contract:
`/v1/capabilities` for portable features, `/v1/backend/actions` for
backend-native tools, and `/v1/backend/config-schemas` for
provider-specific `backend_options`. Typed outputs use core artifact
kinds and versioned `sfmapi.*.v1` interchange formats; backend-native
files stay discoverable through `/v1/backend/artifact-contracts`.
Artifacts can be validated with `/v1/artifacts/{id}:validate` and
converted through normal jobs with `/v1/artifacts/{id}:convert` when
the active backend advertises a conversion path. Existing artifact
files can be registered without copying bytes through
`POST /v1/artifacts:import`.

| Repo | Launcher | Purpose |
|---|---|---|
| `sfmapi_colmap_cli` | `sfmapi-colmap-cli-api` | Original COLMAP CLI backend |
| `sfmapi_pycolmap` | `sfmapi-pycolmap-api` | PyCOLMAP backend with COLMAP CLI fallback |
| `sfmapi_colmap` | `sfmapi-colmap-api` | Native COLMAP/PyCOLMAP/C++ demo backend |
| `sfmapi_realityscan` | `sfmapi-realityscan-api` | RealityCapture/RealityScan CLI action backend |
| `sfmapi_instantsfm` | `sfmapi-instantsfm-api` | InstantSfM Python action backend |
| `sfmapi_spheresfm` | `sfmapi-spheresfm-api` | SphereSfM spherical action backend |

## Plugin hub

`sfm_hub` is bundled as the registry and manifest validator for
backend plugins. Users still interact through `sfmapi`: install or
inspect plugins with the CLI, then discover enabled providers through
the API.

```bash
uv run sfmapi plugins list
uv run sfmapi plugins install colmap_cli --method uv --dry-run
uv run sfmapi plugins install local_test \
  --github https://github.com/SFMAPI/sfmapi_custom.git@v0.1.0 \
  --package sfmapi-custom --dry-run
uv run sfmapi plugins entry-points --load
uv run sfmapi providers list
uv run sfmapi profiles create hybrid --route features=colmap_cli
uv run sfmapi profiles set-default hybrid
uv run sfmapi profiles assign-project 01H... hybrid
```

Operator API equivalents live under `/v1/admin/plugins`. Runtime
discovery lives under `/v1/backend/providers` and `/v1/backend/routing`.
Public SfM job endpoints never install plugins implicitly. HTTP plugin
install execution is dry-run by default and requires
`allow_unsafe_execution=true`; the CLI is the preferred install path.
Installed backend packages should expose
`[project.entry-points."sfmapi.backends"]`. Set
`SFMAPI_AUTO_LOAD_BACKEND_PLUGINS=true` only in worker/operator
processes where importing backend packages is acceptable.

See [docs/](https://sfmapi.github.io/) for the user-facing site,
[SFMAPI-SPEC.md](./SFMAPI-SPEC.md) for the wire spec, and
[CLAUDE.md](./CLAUDE.md) for in-repo conventions.

## Quickstart (standalone — no Docker, no Redis, no Postgres)

The defaults in `.env.example` give you a single-process install:
SQLite file beside the working dir, filesystem blob store, in-process
worker. Drop in a backend package later via `register_backend()`.

```bash
uv venv
uv pip install -e ".[dev]"
cp .env.example .env
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
# In another shell:
curl http://localhost:8080/healthz
curl http://localhost:8080/version
```

The base API does not require Pillow or OpenCV for image metadata.
Install `.[image-processing]` only when this deployment should render
thumbnails or build `dhash` similarity indexes.
Install `.[projection]` when the API process should use the built-in
NumPy/OpenCV pixel engine for equirectangular panorama to cubemap image
jobs. Reverse cubemap rendering and arbitrary perspective views remain
backend-provided contract paths.

For a fully ephemeral, in-memory run (no files written, all state
wiped on shutdown):

```bash
SFMAPI_EPHEMERAL=true uv run uvicorn app.main:app
```

For multi-instance / GPU-fleet deployments: switch
`SFMAPI_QUEUE_BACKEND=arq`, point `SFMAPI_DB_URL` at Postgres, and
run real workers. See `deploy/helm/` for a reference Helm chart.

## MCP / agent setup

sfmapi can expose a curated, read-only FastMCP adapter for agents to
inspect server state, backend capabilities, backend action schemas,
projects, jobs, progress, typed stage artifacts, reconstructions, and
snapshots.

Install the optional dependency and mount MCP into the API process:

```bash
uv sync --extra mcp
uv run sfmapi serve --mcp local --host 127.0.0.1 --port 8000
```

The MCP endpoint is `http://127.0.0.1:8000/mcp`, with a local status
page at `http://127.0.0.1:8000/mcp/status`. Register it with Codex:

```bash
codex mcp add sfmapi_colmap --url http://127.0.0.1:8000/mcp
codex mcp list
```

Or register it with Claude Code:

```bash
claude mcp add --transport http sfmapi_colmap http://127.0.0.1:8000/mcp
claude mcp list
```

Use an underscore in the server name; it avoids shell and config
parsing issues. Existing Codex sessions may need to be restarted before
new MCP servers appear. Existing Claude Code sessions can check MCP
status with `/mcp`.

For backend packages that provide their own API launcher, enable the
same local mount there, for example:

```bash
uv run sfmapi-colmap-api --backend colmap_cpp_native --mcp local
```

The MCP surface is intentionally read-only. Use the REST API or SDKs
for uploads, project creation, pipeline submission, cancellation, and
backend action execution. See the
[MCP adapter guide](https://sfmapi.github.io/guides/mcp.html) for
stdio mode, tenant scoping, and deployment notes.

## Layout

```
app/
  api/v1/        HTTP routes (NEVER imports the SfM backend or other heavy deps)
  core/          config, tenancy, hashing, paths, ids
  db/            SQLAlchemy models + alembic
  schemas/       pydantic I/O models — wire surface
  sources/       ImageSource impls (upload | local | s3)
  storage/       blob store, materializer, snapshot writer
  orchestrator/  in-house Job→Task DAG, lease/janitor, cache lookup
  services/      tenant-scoped CRUD, transactions, DAG construction
  workers/       supervisor + per-task ARQ jobs (subprocess fork)
  adapters/      backend Protocols (backend.py), registry, and the
                 no-op stub. Real engine adapters live in separate repos.
tests/
  unit/          fast, no IO
  integration/   db + filesystem
  e2e/           full app
  contract/      replay recorded fixtures through every SDK
  conformance/   spec-conformance tests
docs/            user docs (Sphinx, published to https://sfmapi.github.io/)
```

Both SQLite and Postgres are supported; CI tests both. AGPL-3.0-or-later.
