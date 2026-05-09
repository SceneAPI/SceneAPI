# Contributing

## Dev loop

```bash
uv venv
uv pip install -e ".[dev]"
cp .env.example .env
uv run alembic upgrade head
uv run pytest -q
uv run uvicorn app.main:app --reload
```

## Running tests under both DB engines

```bash
bash scripts/test_dual_db.sh                      # SQLite + (Postgres if SFMAPI_DB_URL_PG set or docker available)
bash scripts/test_postgres_local.sh               # ephemeral Postgres in docker
```

## Lint + type

```bash
uv run ruff check app tests
uv run ruff format --check app tests
uv run mypy app
```

## Smoke-testing the deploy

```bash
bash scripts/smoke.sh                # bring up compose, walk API, tear down
bash scripts/smoke.sh --keep         # leave stack up on success
```

## Conventional commits

Commit titles drive the changelog (release-drafter). Use:

| Prefix | Maps to release-drafter category |
|---|---|
| `feat:` / `feat(scope):` | 🚀 Features |
| `fix:` / `fix(scope):` | 🐛 Fixes |
| `perf:` | ⚡ Performance |
| `refactor:` / `chore:` | 🛠 Internal |
| `deps:` / `chore(deps):` | 📦 Dependencies |
| `docs:` | 📚 Docs |
| `ci:` | 🤖 CI |
| `feat!:` / `BREAKING CHANGE:` in body | 💥 Breaking |

`scripts/smoke.sh` and the dual-DB tests are the merge gates; if
either is red, the PR doesn't land.

## Adding a new endpoint

1. Pydantic schema under `app/schemas/api/`.
2. Service function under `app/services/`.
3. Route under `app/api/v1/`, mounted from `app/main.py`.
4. Test under `tests/e2e/` (and `tests/integration/` if it touches
   storage).
5. Update the [API reference](../reference/api.md).
6. If the route submits a Task, see "Adding a new SfM stage" below.
7. Re-run `uv run python scripts/regen_sdk.py` so all three SDKs
   pick up the new endpoint.

The web tier must not import any engine library (pycolmap, torch,
cv2, segment_anything, ...); the
`test_app_does_not_import_pycolmap_or_torch` unit test enforces that
boundary.

## Adding a new SfM stage

The most common contribution shape — a new pipeline stage like
`pgo`, `triangulate`, or `mesh`. The post-extraction DX is designed
so the touchpoints are minimal and the drift modes are caught
mechanically.

1. **Add the worker task** at `app/workers/tasks/<kind>.py`. Decorate
   the entry function:

   ```python
   from app.workers.tasks._registry import task_handler

   @task_handler("my_stage")
   def run(task: Task) -> dict:
       inputs, spec = read_state(task)
       backend = get_backend()
       result = backend.my_stage_method(...)
       return {"result_path": ..., **result}
   ```

   Auto-discovery picks it up — no edit to `app/workers/dispatcher.py`
   needed. The decorator raises on duplicate-kind so typos collide
   loudly.

2. **Add the capability string** to
   `app/core/capabilities.py::ALL_KNOWN`. Pick a canonical name
   (`pgo.optimize`, `mesh.poisson`, `<family>.<variant>`).

3. **Add a Protocol method** to `app.adapters.backend.SfmBackend` if
   the stage needs a new backend-side operation. Add the matching
   stub method to `app.adapters.stub_backend.StubBackend` (raises
   `CapabilityUnavailableError(capability="<canonical>")`).

4. **Add a service helper** in `app/services/sfm_stage_service.py`:

   ```python
   async def submit_my_stage(...) -> tuple[str, list]:
       require_capability("my_stage.canonical")     # MUST be in ALL_KNOWN
       # ... assemble inputs dict ...
       return await _submit_single_stage(
           session, tenant_id=tenant_id, project_id=...,
           recipe="my_stage", kind="my_stage",
           inputs=inputs, spec=spec, inline=inline,
       )
   ```

   The capability-consistency test
   (`tests/unit/test_capability_consistency.py`) AST-scans for
   `require_capability("X.Y")` literals and fails if `"X.Y"` is
   missing from `ALL_KNOWN`.

5. **Add a route** in the appropriate `app/api/v1/<resource>.py`,
   delegating to the service helper. Use
   `accepted_response(JobAcceptedResponse(...))` for the 202
   envelope.

6. **Add tests**: at minimum an e2e test that POSTs the route and
   inspects the resulting `Job.status` (the inline queue runs the
   task in-process, so a single test exercises the full
   route → service → worker → backend path).

7. **Update `SFMAPI-SPEC.md`** §6 with the new endpoint, tagged
   `[Extension: <capability>]` if it's optional or `[Core]` if every
   conformant server must implement it. Update
   `docs/reference/api.md` with the route catalog entry.

8. **Regen SDKs** with `uv run python scripts/regen_sdk.py`. The
   contract tests will replay the new fixture through Python +
   TypeScript + C++ on next CI run.

## Adding a new backend or backend method

sfmapi ships no concrete SfM backend; engine packages live in their
own repos and satisfy ``app.adapters.backend.SfmBackend``.

1. Implement the protocol in your backend package; raise
   ``CapabilityUnavailableError`` for ops you don't support and
   advertise the supported subset via ``capabilities()``.
2. Keep `capabilities()` portable. Backend-native commands such as
   `colmap.feature_extractor` or `openmvg.compute_features` belong in
   `list_backend_actions()`, not in `ALL_KNOWN`.
3. Register the factory at app startup:
   ``register_backend("name", MyBackend)``.
4. Add a backend contract test:
   ``assert_backend_contract(MyBackend())`` from
   `app.adapters.backend_contract`. This catches unknown portable
   capabilities, malformed action/config descriptors, duplicate ids,
   non-portable `required_capabilities`, runtime-managed options in
   `backend_options` schemas, and action/config ids leaked through
   `capabilities()`. For a package-level smoke check, run
   `sfmapi check-backend --import my_backend --backend my_backend`.
5. If a new wire op is needed (a method not yet on the protocol),
   add it here in `app/adapters/backend.py` and surface a worker
   task under `app/workers/tasks/` (see "Adding a new SfM stage"
   above). Worker tasks call backends only through
   ``get_backend()``, never via direct import.

Backends advertising a capability that is not in
`app.core.capabilities.ALL_KNOWN` will see that capability silently
dropped at `detect_capabilities` time (logged as a warning); add the
canonical name to `ALL_KNOWN` first only when it is a portable sfmapi
feature. For engine-native tools, add or fix the backend action
descriptor instead.
