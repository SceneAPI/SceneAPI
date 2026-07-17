# Plugin Hub Implementation Checklist

Status: closed for the sfmapi-side registry, entry-point discovery,
install planning, CLI, operator API, benchmark validation, and
provider-routing execution contract.

Open runtime API gaps from the latest review are tracked in
`docs/_internal/plugin_runtime_api_gap_closure_checklist.md`.

## Product Contract

- [x] Keep `sfmapi` as the main interaction point for users.
- [x] Keep `sfm_hub` as the registry, manifest, resolver, and validation package.
- [x] Support multiple installed plugins in one sfmapi environment.
- [x] Support enabled/disabled plugin state separate from installation.
- [x] Support provider priority and named routing profiles in local state.
- [x] Support default, workspace, and project routing profile scopes.
- [x] Require request-level `provider` to override defaults.
- [x] Return a clear ambiguity error when several providers can satisfy a stage.
- [x] Execute portable stage tasks through the resolved provider backend.
- [x] Skip disabled entry-point plugins during backend loading.
- [x] Support provider-specific backend action, config schema, artifact
  contract, artifact conversion, one-shot, and MCP discovery paths.
- [x] Keep plugin install, enable, and doctor operations under CLI or admin API scope.
- [x] Prevent public SfM job APIs from silently installing plugins.

## Implemented Surfaces

- [x] `sfm_hub` Python package.
- [x] `sfm_hub/schemas/backend-plugin.schema.json`.
- [x] `sfm_hub/registry/backends/*/manifest.json`.
- [x] Manifest validation through typed Pydantic models.
- [x] Registry search and info helpers.
- [x] Installed Python entry-point discovery for `[project.entry-points."sfmapi.backends"]`.
- [x] Optional entry-point backend loading through `SFMAPI_AUTO_LOAD_BACKEND_PLUGINS=true`.
- [x] Provider aliases registered into the process-local backend registry.
- [x] GitHub-link parser for plugin sources.
- [x] uv direct-reference install-plan generation.
- [x] Docker install-plan generation for manifests with image/build metadata.
- [x] Container-service install-plan recording for already-running plugin services.
- [x] External-tool install plans and runtime metadata in manifests.
- [x] External tool detection through PATH and configured env vars, including version checks.
- [x] HTTP install execution hardened behind `allow_unsafe_execution=true`; dry-run remains default.
- [x] Plugin doctor checks.
- [x] Conformance metadata fields.
- [x] Benchmark-side plugin validation with `python -m bench.cli plugins`.
- [x] Tests for schema coverage, GitHub parsing, install plans, entry points, API, CLI, and routing.

## Manifest Fields

Every bundled manifest includes `plugin_id`, `display_name`, `description`,
`package_name`, `github_url`, `entry_points`, `providers`, `runtime_modes`,
`capabilities`, `backend_actions`, `config_schemas`, `artifact_contracts`,
`licenses`, `upstream_projects`, `compatibility`, `conformance`, and `trust_tier`.

## GitHub Install Contract

- [x] Registry entries install from GitHub URLs.
- [x] Branch, tag, and commit refs are accepted.
- [x] Mutable refs such as `main` produce warnings.
- [x] uv install commands use direct references.
- [x] uv installs can plan/run a plugin-owned runtime provisioner for release
  downloads, prebuilt assets, or native builds.
- [x] Docker install plans emit `docker pull` or `docker build` commands when metadata exists.
- [x] Container-service install plans record service-mode intent without running shell commands.
- [x] Commit SHA refs are recorded as resolved commits in local state.

Example:

```bash
sfmapi plugins install colmap_cli --method uv --dry-run
sfmapi plugins install local_test \
  --github https://github.com/SFMAPI/sfmapi_custom.git@v0.1.0 \
  --package sfmapi-custom --dry-run
sfmapi plugins install hloc --method uv --no-provision-runtime
```

## sfmapi CLI

- [x] `sfmapi plugins list`
- [x] `sfmapi plugins search <query>`
- [x] `sfmapi plugins info <plugin_id>`
- [x] `sfmapi plugins install <plugin_id> --method uv`
- [x] `sfmapi plugins install <plugin_id> --github <url>`
- [x] `sfmapi plugins install <plugin_id> --method docker`
- [x] `sfmapi plugins install <plugin_id> --method container_service`
- [x] `sfmapi plugins enable <plugin_id>`
- [x] `sfmapi plugins disable <plugin_id>`
- [x] `sfmapi plugins doctor <plugin_id>`
- [x] `sfmapi plugins detect-tools`
- [x] `sfmapi plugins entry-points`
- [x] `sfmapi providers list`
- [x] `sfmapi profiles list`
- [x] `sfmapi profiles create <name>`
- [x] `sfmapi profiles set-default <name>`
- [x] `sfmapi profiles assign-project <project_id> <name>`
- [x] `sfmapi profiles assign-workspace <name>`
- [x] `sfmapi serve --profile <name>`
- [x] `sfmapi check-backend --load-entry-points`

## sfmapi API

- [x] `GET /v1/backend/providers`
- [x] `GET /v1/backend/routing`
- [x] `GET /v1/backend/config-schemas`
- [x] `GET /v1/backend/artifact-contracts`
- [x] `GET /v1/admin/plugins`
- [x] `GET /v1/admin/plugins/entry-points`
- [x] `GET /v1/admin/plugins/{plugin_id}`
- [x] `POST /v1/admin/plugins/{plugin_id}:doctor`
- [x] `POST /v1/admin/plugins/{plugin_id}:install`
- [x] `POST /v1/admin/plugins/{plugin_id}:enable`
- [x] `POST /v1/admin/plugins/{plugin_id}:disable`
- [x] `POST /v1/admin/routing/profiles`
- [x] `POST /v1/admin/routing/default`
- [x] `POST /v1/admin/routing/provider-priority`
- [x] `POST /v1/admin/routing/projects/{project_id}`
- [x] `POST /v1/admin/routing/workspaces`

## Provider Resolution

- [x] Check request-level `provider`.
- [x] Check project-specific routing profile.
- [x] Check workspace-specific routing profile.
- [x] Check the default routing profile.
- [x] Check global provider priority.
- [x] Register resolved provider ids as backend aliases for worker execution.
- [x] Return an ambiguity error with candidate providers and a suggested fix.
- [x] Keep clean installs working with no plugin state and the stub backend.
- [x] Reject combined pair-selection/matching tasks when `pairs.provider` and
  `matcher.provider` resolve to different providers.

## Initial Hub Entries

| Plugin id | Providers | GitHub source | Runtime modes |
|---|---|---|---|
| `colmap_cli` | `colmap_cli` | `https://github.com/SFMAPI/sfmapi_colmap_cli.git` | `uv`, `external_tool` |
| `pycolmap` | `colmap_pycolmap` | `https://github.com/SFMAPI/sfmapi_pycolmap.git` | `uv` |
| `colmap_native` | `colmap_cli`, `colmap_pycolmap`, `colmap_cpp_native`, `colmap_cpp_inmemory` | `https://github.com/SFMAPI/sfmapi_colmap.git` | `uv`, `external_tool` |
| `realityscan_cli` | `realityscan_cli` | `https://github.com/SFMAPI/sfmapi_realityscan.git` | `uv`, `external_tool` |
| `hloc` | `hloc` | `https://github.com/SFMAPI/sfmapi_hloc.git` | `uv` |
| `instantsfm` | `instantsfm` | `https://github.com/SFMAPI/sfmapi_instantsfm.git` | `uv` |
| `spheresfm` | `spheresfm` | `https://github.com/SFMAPI/sfmapi_spheresfm.git` | `uv`, `external_tool` |

## Backend App Adoption Contract

Backend app repositories should expose `plugin.py`, a typed plugin object,
`[project.entry-points."sfmapi.backends"]`, provider ids matching the manifest,
config schemas, artifact contracts, doctor checks, GitHub install metadata,
optional Docker or container-service metadata, an optional
`package.provisioning.provision()` hook for owned runtime setup,
external-tool detection where applicable, and tests for manifest validation,
entry-point discovery, provider registration, and API discovery.

Entry-point plugin objects are executable contracts now, not just
documentation. They may expose `manifest`, `get_plugin_manifest()`,
`register(register_backend)`, `register_backend(register_backend)`,
`backend_factory`, or be a callable backend factory. `sfmapi plugins
entry-points --load`, `sfmapi check-backend --load-entry-points`, and
`python -m bench.cli plugins --require-entry-points` validate adoption.
When a manifest is available, its provider ids are registered as
aliases for the backend factory. If one entry point registers multiple
backend factories, provider ids are matched to factories by name.

## Validation Commands

```bash
uv run pytest tests/unit/test_plugin_hub.py tests/unit/test_plugin_api.py tests/unit/test_plugin_cli.py -q
uv run ruff check sfm_hub app/cli.py app/api/v1/admin.py app/api/v1/backend.py app/schemas/api/plugins.py app/services/plugin_service.py app/services/provider_routing_service.py bench/cli.py tests/unit/test_plugin_hub.py tests/unit/test_plugin_api.py tests/unit/test_plugin_cli.py
uv run python -m bench.cli plugins
uv run sphinx-build -b html docs docs/_build/html
```
