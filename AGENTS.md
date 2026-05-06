# Repository Guidelines

## Project Structure & Module Organization

`app/` contains the FastAPI service: `api/v1/` routes, `schemas/` wire models, `services/` tenant logic, `orchestrator/` scheduling, `workers/` task handlers, `storage/`, `db/`, and `adapters/` for backend registration. Tests use `tests/{unit,integration,e2e,contract,conformance}/`. SDKs live in `clients/python/`, `clients/typescript/`, and `clients/cpp/`; generated output is under `sfmapi_client_gen/` and `src/_generated/`. Docs are in `docs/`, deploy assets in `deploy/`, migrations in `alembic/`, benchmarks in `bench/`.

## Build, Test, and Development Commands

- `uv venv` then `uv sync --extra dev`: create the Python 3.12 environment.
- `uv run alembic upgrade head`: apply database migrations.
- `uv run uvicorn app.main:app --reload`: run the API locally.
- `uv run pytest -q -m "not needs_pycolmap and not needs_postgres"`: run the CI SQLite subset.
- `uv run pytest -q`: run the broader suite when required services/backends exist.
- `uv run ruff check app tests` and `uv run ruff format --check app tests`: lint and format-check.
- `uv run mypy app`: run strict type checks.
- `bash scripts/test_dual_db.sh` or `pwsh scripts/test_dual_db.ps1`: exercise SQLite and Postgres paths.
- SDK checks: `uv run pytest clients/python -q`; from `clients/typescript/`, run `npm test`, `npm run lint`, and `npm run build`; for C++, see `clients/cpp/README.md`.

## Coding Style & Naming Conventions

Python targets 3.12, uses Ruff with a 100-character line length, and keeps mypy strict. Use 4-space indentation, explicit types, snake_case for functions/modules, PascalCase for classes, and UPPER_SNAKE_CASE for constants. Keep routes thin: schemas in `app/schemas/api/`, business logic in `app/services/`, and execution in `app/workers/tasks/`. Do not import concrete SfM engines; backends register through `app.adapters.backend.SfmBackend`.

## Testing Guidelines

Name tests `test_*.py` and place them in the suite matching their blast radius. Use pytest markers from `pyproject.toml`, including `unit`, `integration`, `e2e`, `contract`, and `needs_postgres`. New endpoints should include e2e coverage; storage or database changes need integration coverage. Regenerate SDKs with `uv run python scripts/regen_sdk.py` when OpenAPI changes.

## Commit & Pull Request Guidelines

Use conventional commit titles seen in history, such as `feat:`, `fix:`, `perf:`, `refactor:`, `chore:`, `docs:`, `ci:`, or `feat!:` for breaking changes. PRs should describe behavior changes, list validation commands, link issues, and call out API, migration, SDK, or deployment impacts. Dual-DB and smoke tests are merge gates when relevant.

## Security & Configuration Tips

Start from `.env.example`; keep secrets out of git. Use `SFMAPI_EPHEMERAL=true` for demos, and prefer explicit `SFMAPI_DB_URL`, auth mode, queue backend, and storage settings in shared environments. Backend packages live outside this repo and register through adapters.
