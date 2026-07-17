# CLI / scripts

sceneapi ships the `sceneapi` Python CLI (subcommands listed below;
the pre-rename `sfmapi` alias command remains for one release) plus
several operational scripts under `scripts/` and `deploy/`. None of
them are required for normal use, but each captures a workflow that's
tedious to do by hand.

## `sceneapi` Python CLI

Installed alongside the `sceneapi` package; run via `uv run sceneapi
<subcommand>` or directly once the venv is active. `sceneapi --help`
lists every subcommand; the high-impact ones are noted here.

### `sceneapi serve`

Runs the REST API. Common flags: `--host`, `--port`, `--reload`,
`--profile` (sets the default sfm_hub routing profile), `--mcp` (sets
the API-process MCP mode for this run).

### `sceneapi mcp`

Runs the standalone MCP adapter (`--transport stdio` or `--transport
http`). Defaults read `SCENEAPI_MCP_MODE`.

### `sceneapi check-backend`

Validates a backend's capabilities, actions, and config-schema
contract. `--import sceneapi_my_backend.plugin` registers an entry-point
package before resolving `SCENEAPI_BACKEND`; `--load-entry-points`
loads every installed `[sceneapi.backends]` entry point.

### `sceneapi plugins ...`

Plugin-hub commands (`list`, `search`, `info`, `install`, `enable`,
`disable`, `doctor`, `detect-tools`, `entry-points`). See
`docs/_internal/plugin_hub_checklist.md` in the repository for the
hub working checklist.

### `sceneapi scaffold-plugin <id>`

Generates the smallest viable sceneapi backend plugin tree at
`<output-dir>/sceneapi_<id>/`:

```
sceneapi_<id>/
├── pyproject.toml          # entry point: [project.entry-points."sceneapi.backends"]
├── README.md
├── src/sceneapi_<id>/
│   ├── __init__.py
│   ├── plugin.py           # uses canonical sceneapi.backends.Plugin
│   └── backend.py          # stub satisfies the framework contract
└── tests/
    ├── __init__.py
    └── test_plugin.py
```

The generated `plugin.py` uses {class}`sceneapi.backends.Plugin` --
the canonical entry-point shape -- so the new plugin starts in the
same posture as every baseline plugin and passes
`manifest-valid` immediately.

Flags:

| Flag | Purpose |
|---|---|
| `--output-dir DIR` | Where to create `sceneapi_<id>/`. Defaults to cwd. |
| `--display-name NAME` | Human-readable name. Defaults to TitleCase of `plugin_id`. |
| `--description TEXT` | One-line description for the manifest + README. |
| `--vendor NAME` | Vendor name surfaced in the backend's `runtime_versions`. |
| `--overwrite` | Replace existing files instead of erroring. |

`plugin_id` must match `[a-z][a-z0-9_]*` -- it becomes the package
suffix (`sceneapi_<id>`), the entry-point name, and the backend name.

### `sceneapi scaffold-contract <name>`

Scaffolds an **off-wire core contract** -- a repo-owned data standard
(like the COLMAP scene-database schema) that has no HTTP endpoint but is
parity-checked across the Python and C++ tiers. Generates two files:

```
sceneapi/server/core/<name>.py                    # CONTRACT_NAME + contract_dict()
tests/unit/test_<name>_contract.py    # contract test skeleton
```

The generated module is the source of truth; the cross-tier machinery
(`tools/gen_contracts.py` + the `contract-parity` / `contract-coverage`
check_sync gates) serializes it to a JSON artifact + a C++ `.inc` and
proves the two tiers never diverge.

Flags: `--title`, `--core-dir`, `--tests-dir`, `--overwrite`.

After scaffolding, the command prints the remaining steps -- the one
cross-repo action is registering the contract in
`sfmapi-cpp/tools/gen_contracts.py`'s `CONTRACTS` dict, then running
`gen_contracts.py`. The `contract-coverage` gate fails until the
contract is registered, generated into both tiers, and tested -- so a
new off-wire contract can't be added Python-only.

`name` must match `[a-z][a-z0-9_]*` -- it becomes the `sceneapi/server/core` module
name, the test/artifact filenames, and the C++ accessor stem.

## Shell scripts


## `scripts/test_dual_db.{sh,ps1}`

Runs the test suite under SQLite, then under Postgres
(via `SCENEAPI_DB_URL_PG` or an ephemeral docker container).

## `scripts/test_postgres_local.{sh,ps1}`

Spins up `postgres:16-alpine` in docker on a sacrificial port, runs
migrations, runs the suite, tears down.

## `scripts/smoke.{sh,ps1}`

Brings up the full deploy stack and walks the public API end-to-end
(project → upload → dataset → image). Used by CI's `smoke` job.

## `deploy/install-worker.ps1` / `deploy/uninstall-worker.ps1`

Registers the worker as a Windows service via `nssm`. Multi-GPU =
one service per GPU with distinct names; `-GpuUuid` sets
`CUDA_VISIBLE_DEVICES`.

## `deploy/bootstrap-worker.ps1`

One-shot installer: clones / updates `sfmapi` and the configured
SfM backend repo (set via `COLMAP_MOD_REPO_URL` env var; defaults
to a public pycolmap fork), builds pycolmap, registers the worker
service. Ships in `worker-installer-vX.Y.Z.zip`.
