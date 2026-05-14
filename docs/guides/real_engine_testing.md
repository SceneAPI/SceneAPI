# Real-Engine Testing

sfmapi ships **no concrete SfM backend** (decision L31). The default
test run exercises the wire surface, orchestration shell, and the
no-op `StubBackend` — it never touches a real engine. Tests that *do*
need a real engine are marked and skipped by default.

## Test marker / env-skip matrix

| Marker | Needs | Default run | How to enable |
|---|---|---|---|
| _(unmarked)_ | nothing — stub backend, in-memory SQLite | **runs** | always |
| `needs_postgres` | `SFMAPI_DB_URL` pointed at a live Postgres | skipped | start Postgres, set `SFMAPI_DB_URL=postgresql+psycopg://…`, run `-m "not needs_pycolmap"` |
| `needs_pycolmap` | a registered backend that advertises real SfM capabilities (a `pip install`-ed plugin) | skipped | install a backend package (e.g. `sfmapi-pycolmap`), then run without `-m "not needs_pycolmap"` |
| `needs_backend` | any concrete `SfmBackend` registered (broader than `needs_pycolmap`) | skipped | install + register any backend plugin |

The canonical default invocation is:

```bash
uv run pytest -q -m "not needs_pycolmap and not needs_postgres"
```

CI's `test-sqlite` and `test-postgres` jobs both apply
`not needs_pycolmap` — neither builds an engine. `needs_backend` is a
superset gate for tests that need *any* engine, not COLMAP
specifically.

## Running the real-engine suite locally

A backend plugin lives in its own repo (`sfmapi_pycolmap`,
`sfmapi_colmap_cli`, `sfmapi_hloc`, …). To exercise the
`needs_pycolmap` / `needs_backend` tests:

```bash
# 1. install a backend into the same venv as sfmapi
uv pip install -e ../sfmapi_pycolmap        # or another backend repo

# 2. run the full suite — the backend auto-registers via its
#    sfmapi.backends entry point at app startup
uv run pytest -q -m "not needs_postgres"
```

Each backend repo also has its own suite gated on the engine actually
being present (`needs_colmap`, `needs_sample_data`, …) — see that
repo's `pyproject.toml` `[tool.pytest.ini_options].markers`.

## Real-engine CI lane

`.github/workflows/ci.yml` carries a `real-engine` job that is
**`workflow_dispatch`-only** — it does not run on every push/PR
because building COLMAP from source + downloading the South Building
sample is a long, resource-heavy job. Trigger it manually from the
Actions tab when validating a change that could affect real-engine
behavior (worker task wiring, the backend Protocol surface, the
materialization path). The job:

1. builds / installs COLMAP,
2. installs a COLMAP-backed sfmapi plugin,
3. downloads the South Building sample dataset,
4. runs `uv run pytest -q -m "needs_pycolmap or needs_backend"`.

It is intentionally a skeleton: the COLMAP build step is the slow,
environment-specific part and is expected to be tuned per runner.
Until it is hardened, treat a green default CI + a successful local
real-engine run as the bar for engine-affecting changes.
