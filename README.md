# sfmapi

**A generic HTTP/REST API for Structure-from-Motion tasks.** Backend-
agnostic by design: any SfM engine that conforms to the spec can serve
it (pycolmap, OpenSfM, hloc, custom forks). Sealed-snapshot progress,
content-addressed storage, multi-tenant from day 1.

This repository ships the **wire spec + orchestration shell only** —
no concrete SfM engine. Backend implementations live in their own
repositories, satisfy `app.adapters.backend.SfmBackend`, and register
at startup via `register_backend("name", Backend)`. A no-op
`StubBackend` is bundled for tests and `SFMAPI_EPHEMERAL=true` demos.

See [docs/](https://sfmapi.github.io/) for the user-facing site,
[SFMAPI-SPEC.md](./SFMAPI-SPEC.md) for the wire spec, and
[CLAUDE.md](./CLAUDE.md) for in-repo conventions.

## Quickstart

```bash
uv venv
uv pip install -e ".[dev]"
cp .env.example .env
uv run alembic upgrade head
uv run pytest -q
uv run uvicorn app.main:app --reload
```

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
  adapters/      backend Protocol (backend.py), registry, and the
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
