# Changelog

All notable changes to **sfmapi** are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

`Unreleased` is auto-populated by
[release-drafter](https://github.com/release-drafter/release-drafter)
based on merged PR labels (see `.github/release-drafter.yml`). At
release time, the drafted notes are promoted to a versioned section
below and a new `Unreleased` block is started.

## [Unreleased]

_Drafted by release-drafter from merged PRs since the last tag._

## [0.0.2] - 2026-07-17

The lean-audit remediation release (see
`docs/guides/lean_audit_2026.md` for the full ledger and
`docs/guides/decisions.md` L37-L44 for the decisions).

### Added
- Preview conformance tier: admin routing, dataflow/processor
  discovery, and similarity are fenced from the default OpenAPI
  contract (136 -> 123 ops) behind `SFMAPI_EXPOSE_PREVIEW_APIS`;
  routes still serve.
- Opt-in retention GC (`SFMAPI_RETENTION_DAYS`) for terminal job
  records; composite task index for lease/sweep predicates.
- `sfmapi.plugin_service`: the supported container-plugin kit
  (protocol 1.1), adopted by the radiance provider family.
- `sfmapi.contracts.colmap_db`: the COLMAP schema contract moved to
  the public namespace (deprecation shim at the old path).
- Golden-bytes parity fixture + tests for `application/x-sfm-points-v1`
  across the server and generated SDK parsers.

### Changed
- **Breaking (pre-1.0):** the 8 snake_case custom verbs renamed to
  lowerCamel per AIP-136 (`:fromArchive`, `:fromVideo`,
  `:importKapture`, `:projectImages`, `:renderCubemap`,
  `:renderEquirectangular`, `:renderPerspective`, `:toCubemap`).
- **Breaking (internal namespace):** the `app` package moved to
  `sfmapi.server`; a deprecation shim keeps `app.*` imports working
  until 0.1.0.
- `POST /v1/projects/{id}/pipelines/{recipe}` is deprecated; use
  `pipelines:run`.
- Oneshot endpoints run engine work off the event loop; the web
  import graph no longer pulls numpy at startup.
- Dependency readiness single-sourced ({succeeded, skipped}); janitor
  sweeps are SQL-scoped and per-phase fault-isolated.

### Deprecated
- The hand-rolled Python SDK (`sfmapi_client`) is deprecated as of
  this release (L12); removal at 0.1.0. The `app.*` import namespace
  is deprecated; removal at 0.1.0.

### Removed
- Depth/normal binary parsers from the supported generated SDKs (the
  formats were never on the wire); reintroduction requires a server
  emitter and spec entry.

### Added
- Added `GET /v1/jobs/{id}/progress`, a compact polling snapshot for
  job status, task counts, latest progress event, active task, and
  best-effort overall progress.
- Added an optional backend `ProgressReporter` contract. Long-running
  backend methods may accept `progress=` to emit durable
  `ProgressEvent` telemetry without breaking existing backend
  implementations.
- Added an optional FastMCP adapter, `sfmapi-mcp` entrypoint, and
  `SFMAPI_MCP_ENABLED` FastAPI mount for local agent access over stdio
  or HTTP, including local HTML status pages.
- Added MCP read-only tool annotations, resource templates, server
  instructions, and a non-loopback HTTP opt-in guard.
- Scoped MCP tenant access with `SFMAPI_MCP_TENANT_ID` so API-key
  deployments cannot use MCP as a cross-tenant read bypass.
- Added a backend action catalog at `/v1/backend/actions` so
  backend-native tools can be discovered, validated, and submitted as
  normal jobs without leaking tool-specific ids into portable
  capability flags.
- Added `SFMAPI_MCP_MODE` and the `sfmapi serve --mcp local` /
  `sfmapi mcp` commands for a clearer local agent setup, while keeping
  `SFMAPI_MCP_ENABLED=true` as a compatibility alias.

### Changed
- Moved the server implementation from the top-level `app` package into
  the `sfmapi` namespace as `sfmapi.server` (console scripts, the ARQ
  worker entrypoint, and `uvicorn` targets now use `sfmapi.server.*`
  module paths). The wire contract is unchanged.
- Reorganized the published documentation homepage and sidebar around
  user journeys: start, API usage, backend implementation, operations,
  SDKs, specification, and contribution.
- Cleaned up SDK documentation to distinguish generated Python and
  TypeScript surfaces from the header-only C++ client.
- Removed internal design notes, AIP audits, proposals, and legacy
  Python client API pages from the public site while keeping them in
  the repository for development history.
- Clarified authentication, admin-route, quota, backend-output, and
  runtime-version documentation to match current implementation
  behavior.

### Deprecated
- The top-level `app` package is now a compatibility alias over
  `sfmapi.server`: every `app.*` import keeps working and resolves to
  the same module objects, but emits a `DeprecationWarning`. The alias
  is removed in 0.1.0 — plugins should use the public `sfmapi.*`
  facades; internal tooling should import `sfmapi.server.*`.

## [0.0.1] - 2026-05-02

### Added
- Phase 0 skeleton: FastAPI app, tenancy scaffold (`tenant_id` everywhere),
  blob store, chunked upload, projects/datasets/images CRUD,
  `runtime_versions` table.
- Phase 1 orchestrator + workers: in-house Job→Task DAG, ARQ executor,
  per-task lease/heartbeat, sealed-snapshot writer, ProgressEvent v1
  schema, SSE streaming for `/v1/jobs/{id}/events`. SfM stage endpoints
  for `features`, `matches`, `verify`.
- Phase 2 incremental SfM: `IncrementalSpec` discriminated union,
  `MappingInput` checkpoint primitives, standalone `bundle_adjust`,
  `triangulate`, `relocalize`, `pgo`, `export`, paginated reads,
  binary points format (`application/x-sfm-points-v1`).
- Phase 3 segmentation: SAM lazy adapter, `MaskSet` model,
  `model_artifact` registry with sha-verified install.
- Phase 4 recipes: `pipelines/{incremental|global|hierarchical|spherical}`
  sugar endpoint that builds a 4-node DAG.
- Phase 5 production hardening: S3 source GA + global LRU cache,
  fair-share scheduler, Prometheus metrics, full resume from
  `MappingInput`, API-key auth, structured logging with per-job
  `log.jsonl`, snapshot/job GC.
- Deployment: `deploy/docker-compose.yml` (web + redis + postgres),
  `deploy/Dockerfile.web`, `deploy/install-worker.ps1` +
  `deploy/bootstrap-worker.ps1` (Windows + CUDA), `deploy/README.md`,
  `worker-installer.zip` produced at release time.
- CI: `ci.yml` (lint, test-sqlite, test-postgres, smoke),
  `release.yml` (GHCR + GH release + worker-installer.zip),
  `worker-tests.yml` (self-hosted GPU runner, real pycolmap),
  `dependabot.yml`, `renovate.json` (tracks `colmap_mod` ref).

[Unreleased]: https://github.com/sfmapi/sfmapi/compare/v0.0.1...HEAD
[0.0.1]: https://github.com/sfmapi/sfmapi/releases/tag/v0.0.1
