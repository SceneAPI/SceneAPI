# sfmapi

**A generic HTTP/REST API for Structure-from-Motion tasks.** Backend-
agnostic by design: any SfM engine that conforms to the spec can serve
it (pycolmap, OpenSfM, hloc, custom forks). Sealed-snapshot progress,
content-addressed storage, multi-tenant from day 1.

The reference implementation in this repo wires `app.adapters.colmap_backend.ColmapModBackend`
to a pycolmap fork — but the wire surface (REST routes, request/response
schemas, error envelope, capability discovery) is engine-independent.
A different backend swaps in via a single
`register_backend("name", Backend)` call; nothing else in the codebase
needs to change.

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
  adapters/      ONLY heavy-dep importers (pycolmap / torch / cv2);
                 backend-agnostic SfmBackend Protocol lives in backend.py
tests/
  unit/          fast, no IO
  integration/   db + filesystem
  e2e/           full app
  contract/      replay recorded fixtures through every SDK
  conformance/   spec-conformance tests
docs/            user docs (Sphinx, published to https://sfmapi.github.io/)
```

Both SQLite and Postgres are supported; CI tests both. AGPL-3.0-or-later.
