# Repository Guidelines

## Project Structure & Module Organization

`app/` contains the FastAPI service: `api/v1/` routes, `schemas/` wire models, `services/` tenant logic, `orchestrator/` scheduling, `workers/` task handlers, `storage/`, `db/`, and `adapters/` for backend registration. Tests use `tests/{unit,integration,e2e,contract,conformance}/`. SDKs live in the sibling `../sfmapi-sdk` repository; `scripts/regen_sdk.py` copies this repo's OpenAPI document there and regenerates client code. Docs are in `docs/`, deploy assets in `deploy/`, migrations in `alembic/`, benchmarks in `bench/`.

## Build, Test, and Development Commands

- `uv venv` then `uv sync --extra dev`: create the Python 3.12 environment.
- `uv run alembic upgrade head`: apply database migrations.
- `uv run uvicorn app.main:app --reload`: run the API locally.
- `uv run pytest -q -m "not needs_postgres"`: run the CI SQLite subset.
- `uv run pytest -q`: run the broader suite when required services/backends exist.
- `uv run ruff check app sfmapi sfm_hub tests scripts` and `uv run ruff format --check app sfmapi sfm_hub tests scripts`: lint, import-sort, and format-check (same scope as CI).
- `bash scripts/test_dual_db.sh` or `pwsh scripts/test_dual_db.ps1`: exercise SQLite and Postgres paths.
- SDK checks from `../sfmapi-sdk`: `uv run --extra dev pytest -q` in `python/`, `npm test`, `npm run lint`, and `npm run build` in `typescript/`, and `cmake -S . -B build -DSFMAPI_CPP_TESTS=ON && cmake --build build && ctest --test-dir build -C Debug` in `cpp/`.

## Coding Style & Naming Conventions

Python targets 3.12 and uses the Ruff stack with a 100-character line length. Use 4-space indentation, explicit types where they clarify contracts, snake_case for functions/modules, PascalCase for classes, and UPPER_SNAKE_CASE for constants. Keep routes thin: schemas in `app/schemas/api/`, business logic in `app/services/`, and execution in `app/workers/tasks/`. Do not import concrete SfM engines; backends implement the protocols in `sfmapi.backends` and register via `sfmapi.runtime.register_backend` (container-service plugins build on `sfmapi.plugin_service`; the `app.*` tree is internal).

## Testing Guidelines

Name tests `test_*.py` and place them in the suite matching their blast radius. Use pytest markers from `pyproject.toml`, including `unit`, `integration`, `e2e`, `contract`, and `needs_postgres`. New endpoints should include e2e coverage; storage or database changes need integration coverage. Regenerate SDKs with `uv run python scripts/regen_sdk.py` when OpenAPI changes.

## Commit & Pull Request Guidelines

Use conventional commit titles seen in history, such as `feat:`, `fix:`, `perf:`, `refactor:`, `chore:`, `docs:`, `ci:`, or `feat!:` for breaking changes. PRs should describe behavior changes, list validation commands, link issues, and call out API, migration, SDK, or deployment impacts. Dual-DB and smoke tests are merge gates when relevant.

## Security & Configuration Tips

Start from `.env.example`; keep secrets out of git. Use `SFMAPI_EPHEMERAL=true` for demos, and prefer explicit `SFMAPI_DB_URL`, auth mode, queue backend, and storage settings in shared environments. Backend packages live outside this repo and register through adapters.
