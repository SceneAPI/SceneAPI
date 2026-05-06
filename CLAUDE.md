# CLAUDE.md

Guidance for Claude Code working in this repository.

## Project Overview

`sfmapi` is the **wire spec + orchestration shell** for SfM-as-a-service.
It is a generic HTTP/REST API for Structure-from-Motion tasks; backend
implementations (pycolmap forks, OpenSfM, hloc, custom engines) live in
**separate packages** and register at startup via
`app.adapters.registry.register_backend("name", Backend)`.

The repo ships:
- A FastAPI web tier with no engine-library imports.
- Decomposed pipeline endpoints
  (`features → matches → verify → map → ba → triangulate → relocalize → pgo`)
  + recipe sugar (`/pipelines/{incremental|global|hierarchical|spherical}`).
- Sealed-snapshot progress feed for light interactivity during long-running runs.
- Three SDKs (Python, TypeScript, C++) generated from the same OpenAPI spec.
- A no-op `StubBackend` (`app.adapters.stub_backend`) for tests + the
  `SFMAPI_EPHEMERAL=true` self-contained demo runtime.

There is **no** concrete SfM engine in this repo. Routes that need a backend
return `501 CapabilityUnavailableError` until a real backend package is
installed and registered.

## Decision Register

Single-page index of every locked decision, cancelled item with
rationale, and open proposal awaiting user input lives at
`docs/guides/decisions.md`. Read that first before reopening any
architectural conversation; the cross-links in that file are the
canonical source for "what is settled here?".

## Locked Constraints

1. **Deploy unit = one GPU per instance.** Scale = more instances, not more
   workers per GPU. Per-GPU concurrency for SfM tasks = 1.
2. **v0 single-user, multi-tenant-ready from day 1.** `tenant_id NOT NULL
   DEFAULT 'default'` on every table, workspace path prefixed by tenant,
   `current_tenant()` FastAPI dep returns `'default'` until auth lands.
3. **Image inputs**: `upload | local | s3` behind one `ImageSource` abstraction.
   Local 50GB dirs MUST NOT be copied; S3 pre-downloads to global LRU cache.
4. **Batched + sealed-snapshot progress.** API NEVER reads live `database.db`
   or live `sparse/`; only sealed `snapshots/{seq}/` written by the worker via
   atomic dir rename.

## Locked Tech Decisions

- **DAG**: in-house orchestrator (DAG construction, lease/janitor, cache
  lookup, cancellation) + ARQ as the per-task executor. One Task = one ARQ
  job. Don't encode DAG edges in ARQ enqueue chains.
- **Storage backends are pluggable.**
  - `BlobStore` (`app/storage/blobs.py`) is a Protocol; `get_blob_store()`
    chooses `FSBlobStore` (default), `S3BlobStore`, or `InMemoryBlobStore`
    from `SFMAPI_BLOB_BACKEND` (`fs`|`s3`|`memory`). Callers must use
    `local_path(sha)` (cross-backend) rather than `path_for(sha)` (FS-only).
  - `Queue` (`app/orchestrator/queue.py`) is a Protocol; `get_queue()`
    chooses `ArqQueue` (default) or `InlineQueue` from
    `SFMAPI_QUEUE_BACKEND` (legacy `SFMAPI_INLINE_TASKS=true` still forces
    inline). All enqueue paths go through the protocol — never construct
    an ARQ pool directly.
  - Task execution is queue-agnostic: ``app/workers/dispatcher.py``
    holds ``execute_task(task_id)`` (lease + heartbeat + handler dispatch
    + status transitions). ``app/workers/runner.py`` is now a thin ARQ
    shim that calls into the dispatcher; new queue backends (Celery,
    SQS) wrap ``execute_task`` the same way.
  - Worker tasks never inline their own materialization logic — use
    ``app.workers._materialize.materialize_image_set()`` (full set) or
    ``resolve_image_path()`` (single image). Adding kind-specific
    handling there reaches every task automatically.
- **Ephemeral mode** (`SFMAPI_EPHEMERAL=true`) — single-process, zero
  persistence: in-memory SQLite (`StaticPool`) + `InMemoryBlobStore` +
  `InlineQueue` + `tempfile.mkdtemp()` workspace, schema bootstrapped on
  startup, tempdir wiped on shutdown. Intended for demos, embedded use,
  and smoke tests; do not enable on multi-worker / multi-instance
  deploys (in-memory state is per-process).
- **DB**: SQLite for v0, Postgres-compatible. ANSI SQL only. No `JSONB`, no
  `SKIP LOCKED`, no `RETURNING` reliance, no Postgres-only triggers. CI runs
  the test suite under both.
- **Upload**: roll-our-own chunked. `POST /uploads → upload_id`,
  `PATCH /uploads/{id}` with `Content-Range`, `POST /uploads/{id}/finalize`.
  `Idempotency-Key` from day 1.
- **Points serialization**: binary, fixed-width 26 B/point + 32 B header
  (see `app/schemas/points_binary.py`). `Content-Type:
  application/x-sfm-points-v1`. Cursor pagination via HTTP `Range`.
- **Realtime**: SSE-only for v0 (events + log replay via `Last-Event-ID`).
  Reserve `/ws/v1/...` route prefix; WebSocket added later.

## Layout

```
app/
  main.py                FastAPI app, lifespan, router registration
  api/v1/                HTTP — never imports pycolmap/torch
    health.py            /healthz /readyz /version /metrics
    projects.py datasets.py images.py masksets.py uploads.py
    jobs.py reconstructions.py submodels.py pipelines.py
  core/
    config.py            Pydantic Settings (SFMAPI_* env vars)
    tenancy.py           current_tenant() dep, TenantScopedSession
    hashing.py           canonical-json + content-address helpers
    paths.py             tenant-aware workspace path builder
    ids.py               ULID factory
    errors.py            error classes + handlers
    logging.py           structlog config
  db/
    base.py              SQLAlchemy declarative base, naming convention
    session.py           async engine + session factory
    models.py            ORM models (one file initially; split if needed)
    types.py             ULID type, JSON type, helpers
  schemas/
    pipeline_spec.py     IncrementalSpec | GlobalSpec | HierarchicalSpec |
                         SphericalSpec discriminated union; each version=Literal[1]
    progress_event.py    versioned event vocabulary
    points_binary.py     26 B/record + 32 B header binary format
    api/                 request/response models per resource
  sources/
    base.py              ImageSource protocol, ImageMaterialization
    upload.py            UploadSource (sfmapi owns bytes via blob store)
    local.py             LocalPathSource (fingerprint, no copy)
    s3.py                S3Source (lazy download to LRU cache; Phase 5 GA)
  storage/
    blobs.py             content-addressed blob store + refcount
    snapshots.py         atomic-rename sealed snapshot writer
    workspace.py         workspace path / GC
  orchestrator/
    dag.py               Job→Task DAG construction
    scheduler.py         lease + cache-lookup + enqueue
    cancel.py            cooperative cancel-flag + hard-kill protocol
    janitor.py           reclaim expired leases
  services/              tenant-scoped CRUD; uses sessions + storage/orchestrator
    project_service.py dataset_service.py image_service.py
    job_service.py reconstruction_service.py
  workers/
    supervisor.py        per-GPU; forks subprocess per Task; lease refresh
    runner.py            ARQ entrypoint (settings + worker bootstrap)
    events.py            ProgressEvent emitter → events.jsonl + Redis stream
    tasks/
      extract.py match.py verify.py map.py ba.py triangulate.py
      relocalize.py pgo.py export.py segment.py
  adapters/              backend Protocol + registry only — no engine imports
    backend.py           SfmBackend Protocol (the contract every backend implements)
    registry.py          register_backend() + get_backend()
    stub_backend.py      no-op stub used by tests + SFMAPI_EPHEMERAL=true
    image_adapter.py     PIL + EXIF (pure-python, no engine dep)
tests/
  unit/                  fast, no IO
  integration/           hits db + filesystem
  e2e/                   full app
  conftest.py            shared fixtures (tmp workspace, in-memory db, ...)
docs/
  phase_0_skeleton.md
  phase_1_orchestrator_features_match.md
  phase_2_incremental_sfm.md
  phase_3_segmentation.md
  phase_4_global_spherical.md
  phase_5_resume_tenancy_s3_obs.md
alembic/                 migrations (dual-dialect, SQLite + Postgres)
scripts/                 dev / ops scripts
```

## Conventions

### Imports
- Web layer (`app/api/`, `app/main.py`) **never** imports `pycolmap`, `torch`,
  `segment_anything`, or `cv2`. The web process must start in <2s.
- `adapters/` is the **only** module that imports those. Adapters are
  **sync**.
- Workers (`app/workers/`) call adapters via `anyio.to_thread.run_sync` or
  via fork-per-task subprocess (the supervisor model).
- `services/` calls `storage/`, `orchestrator/`, and `db/`. It does **not**
  import `adapters/` directly.

### Database
- ULIDs as `CHAR(26)` strings. No `BIGSERIAL`.
- Every table has `tenant_id CHAR(26) NOT NULL DEFAULT 'default'`,
  `created_at`, `updated_at`.
- JSON via SQLAlchemy `JSON` (do not query inside JSON; pull and filter in
  Python — keeps SQLite/Postgres parity).
- Lease pattern (works on both engines):
  ```python
  result = await session.execute(
      update(Task)
      .where(Task.task_id == tid,
             or_(Task.lease_expires_at.is_(None),
                 Task.lease_expires_at < now()))
      .values(lease_expires_at=now() + LEASE_TTL, worker_id=worker_id)
  )
  acquired = result.rowcount == 1
  ```
- All Alembic migrations dialect-neutral. Use `op.create_index` without
  `postgresql_*` kwargs; if you must, branch on `op.get_bind().dialect.name`.

### IDs
- Generate with `app.core.ids.new_id()` → returns 26-char ULID string. Sortable.

### Errors
- Domain errors subclass `app.core.errors.SfmApiError`. FastAPI exception
  handler maps to RFC7807 problem+json. Never raise raw `HTTPException` from
  services.

### Tenancy
- Routes get `tenant_id: str = Depends(current_tenant)`. Services accept
  `tenant_id` as the first arg. Repositories filter on `tenant_id` at the
  query level — never trust the caller to add `WHERE`.

### Hashing
- `canonical_json(obj)` → `bytes` (sorted keys, no whitespace) for stable
  hashing of Pydantic models.
- Content addresses are `sha256` lowercase hex. Blob path =
  `blobs/{sha[:2]}/{sha}`.
- Cache key for a Task = `H(canonical_json({inputs_hash, params_hash,
  runtime_version_id, kind}))`.

### Testing (TDD)
- Every change starts with a failing test. Order: unit → integration → e2e.
- Pytest markers: `unit`, `integration`, `e2e`, `conformance`, `contract`,
  `needs_pycolmap`, `needs_postgres`. Default run skips `needs_pycolmap`
  and `needs_postgres` unless those are explicitly available.
- **Contract tests** (`tests/contract/`) boot the app in ephemeral mode,
  record representative responses to `tests/contract/fixtures/`, and
  replay them through every SDK's typed surface (Pydantic in Python,
  TS interfaces, C++ POD structs via `test_contract.cpp`). Catches
  semantic drift the static type-shape diff misses (renames, default
  flips, discriminator changes on tagged unions). Adding a new fixture
  = adding a recording in `test_a_record_fixtures.py` and a decode
  assertion downstream. `test_c_openapi_typing_guards.py` enforces
  that selected routes keep a typed `response_model` in the OpenAPI
  spec (so SDK codegen doesn't fall back to `Any`).

### Routes must declare `response_model`
- Every endpoint that returns a JSON body MUST set
  `response_model=...` (or return a Pydantic model with annotated
  return type). Returning a raw `JSONResponse(dict_body)` makes the
  response untyped in the OpenAPI spec; SDK codegen then falls back
  to `Any` and clients lose all typing for that route. The canonical
  202 envelope for any job-submitting endpoint is
  :class:`app.schemas.api.jobs.JobAcceptedResponse`.
- The 16 remaining "untyped" routes are intentionally non-JSON:
  204 deletes, binary file streams (`*.bin`, `bytes`, `thumbnail`),
  the SSE event stream, and large precomputed JSON files served as
  `FileResponse`. The
  :func:`tests.contract.test_c_openapi_typing_guards.test_no_regression_in_untyped_route_count`
  guard fails if that count grows — when adding a new genuinely
  non-JSON endpoint, bump the limit; otherwise add `response_model=`.

### Generated SDKs (Python + TypeScript)
- ``scripts/regen_sdk.py`` is the single entrypoint: dumps the
  current OpenAPI spec, runs ``openapi-python-client`` for Python,
  then ``openapi-typescript`` for TS. Outputs land in:
  - ``clients/python/sfmapi_client_gen/`` — attrs-based dataclasses
    plus per-endpoint API methods.
  - ``clients/typescript/src/_generated/openapi.d.ts`` — type-only
    paths/components, plus a thin ``openapi-fetch`` runtime wrapper
    in ``src/_generated/client.ts``.
- Both generated SDKs ship with a small repo-owned ergonomics shim
  that mirrors the hand-rolled SDK: typed ``SfmApiError`` hierarchy,
  ``supports()`` capability helper, chunked-upload convenience
  (init→patch→finalize → ``blob_sha``), Server-Sent Events
  iterator over ``GET /v1/jobs/{id}/events`` honoring
  ``Last-Event-ID`` for resume, and pure-stdlib parsers for the three
  binary wire formats (``application/x-sfm-points-v1``,
  ``application/x-sfm-depth-v1``, ``application/x-sfm-normal-v1``):
  - Python: ``clients/python/sfmapi_client_gen/_ergonomics.py`` —
    typed exception classes + ``raise_for_status(UnexpectedStatus)``
    + ``buildhttp_error(httpx.Response)`` + ``supports(caps, name)``
    + ``upload_bytes(base_url, data, ...)`` / ``upload_file(...)``
    + ``stream_events(base_url, job_id, ...)`` /
    ``parse_sse_buffer(body)`` + ``parse_points_binary(data)`` /
    ``parse_depth_map(data)`` / ``parse_normal_map(data)``.
  - TypeScript: ``clients/typescript/src/_generated/ergonomics.ts`` —
    same hierarchy as ES classes + ``buildSfmApiError`` /
    ``raiseForStatus`` / ``supports`` + ``uploadBytes(data, opts)``
    + ``streamEvents(jobId, opts)`` / ``parseSseBuffer(body)``
    + ``parsePointsBinary(buf)`` / ``parseDepthMap(buf)`` /
    ``parseNormalMap(buf)``. Re-exported through the
    ``@sfmapi/client/generated`` subpath.
- Cross-language parity for the binary formats is enforced by
  Python contract tests that round-trip server-encoded bytes
  through the generated parser and TS contract tests that
  synthesize equivalent payloads via ``DataView``.
- All three SDKs expose a ``wait_for_job`` / ``waitForJob`` /
  ``WaitForJob`` helper that polls ``GET /v1/jobs/{id}`` until the
  job reaches a terminal status (succeeded / failed / cancelled /
  cancelled_dirty) and returns the final ``JobDetail`` body. The
  Python and TS helpers accept an optional ``on_event`` / ``onEvent``
  callback that fires for every new ``ProgressEvent`` observed; the
  C++ helper takes a sleep callback (``std::function<void(int)>``)
  so consumers can plug in their own scheduler instead of
  ``std::this_thread::sleep_for``. C++ also exposes ``ParseJobDetail``
  / ``JobDetailFromJson`` / ``TaskRowFromJson`` for typed access to
  job bodies. The helpers depend on
  ``app/workers/dispatcher.py::_maybe_finalize_job`` rolling
  ``Job.status`` up from its constituent ``Task`` rows on every task
  transition; do NOT remove that rollup without also reworking the
  helpers.
- All three SDKs also expose a ``submit_and_wait`` / ``submitAndWait``
  / ``SubmitAndWait`` combinator that takes a stage-submit callable
  and chains directly into ``WaitForJob``. This is the canonical
  end-to-end consumer flow: submit the stage, block until terminal,
  return the typed ``JobDetail``. The Python and TS variants accept
  any closure returning a ``JobAcceptedResponse``-shaped value; the
  C++ variant takes a ``std::function<HttpResponse()>`` so any
  ``Submit*`` method can be passed via ``[&]() { return SubmitX(...); }``.
- A live-streaming ``submit_and_stream`` / ``submitAndStream`` /
  ``SubmitAndStream`` recipe is also available across all three
  SDKs. It submits the job, consumes the SSE event stream live, and
  hands back the terminal ``JobDetail``. Python returns a generator
  that yields each ``SseEvent`` and stores the final detail in its
  ``StopIteration.value`` (PEP 380); TS returns a
  ``SubmitAndStreamHandle`` with separate ``events`` (async
  iterable) and ``result`` (Promise<JobDetail>) fields; C++ takes an
  ``on_event`` callback because C++17 has no generators.

### Hand-rolled SDKs are deprecated
- ``clients/python/sfmapi_client/`` is **deprecated** as of 0.0.2.
  Importing it now emits a single :class:`DeprecationWarning` on
  first import. The full ergonomics surface (typed exceptions,
  ``supports()``, ``upload_bytes()`` / ``stream_events()`` /
  ``parse_points_binary()`` / ``wait_for_job()`` /
  ``submit_and_wait()`` / ``submit_and_stream()``) is reproduced in
  ``sfmapi_client_gen._ergonomics``. New consumers should pick the
  generated SDK; existing imports continue to work until a future
  major-version cleanup. Migration guidance lives in the package
  docstring.

### TypeScript OO methods on the generated client
- ``createSfmApiClient(opts)`` returns an ``SfmApiGeneratedClient``
  with ``uploadBytes`` / ``streamEvents`` / ``waitForJob`` /
  ``submitAndWait`` / ``submitAndStream`` / ``parseEventsBuffer``
  exposed as instance methods. Each method binds the client's
  configured ``baseUrl`` / ``apiKey`` / ``fetch`` so callers don't
  repeat them per call. The raw ``openapi-fetch`` paths client is
  still reachable via ``client.raw`` for any non-ergonomic typed
  call.

### C++ ``Client`` OO surface
- The C++ ``Client`` mirrors the same shape: every helper
  (``UploadBytes``, ``UploadFile``, ``StreamEvents``,
  ``GetJobEvents``, ``WaitForJob``, ``SubmitAndWait``,
  ``SubmitAndStream``) is an instance method bound to the client's
  ``base_url`` / ``api_key``. ``StreamEvents(job_id)`` fetches and
  decodes the buffered SSE body in one call; consumers wanting true
  streaming drive the SSE parser themselves with their HTTP library
  (libcurl ``CURLOPT_WRITEFUNCTION`` etc).

### SSE stream termination
- ``GET /v1/jobs/{id}/events`` closes its SSE stream once the job's
  status reaches a terminal value AND one final drain cycle has
  shipped any pending events. Without this exit condition,
  ``submit_and_stream`` consumers block forever on a job that
  already finished. The terminal vocabulary is shared with
  ``app/workers/dispatcher.py::_maybe_finalize_job``.

### C++ live-server testing intentionally omitted
- C++ ships no built-in HTTP transport (consumers BYO libcurl /
  cpp-httplib / Emscripten Fetch / WinHTTP), so a portable live
  test would either mandate a transport choice in CI or maintain
  one inline. The cost is high: ``windows.h`` macro pollution
  (``min``/``max``), ``CreateProcess`` env-var inheritance quirks,
  and process-orphan cleanup all bit during the prototype.
- Coverage equivalence is preserved through two existing
  mechanisms: (1) ``test_contract.cpp`` decodes the same
  Python-recorded fixtures the Python live test produces, proving
  wire-shape parity end-to-end; (2) ``test_client.cpp`` uses
  Recorder transports to verify every Client method's URL +
  headers + body construction. A C++ live test would only prove
  "the bound transport works" — which is the consumer's transport,
  not the SDK's.

### TypeScript live-server contract test
- ``clients/typescript/test/generated_live.test.ts`` spawns the
  ephemeral app via ``uv run python -m uvicorn`` on a random port
  and runs three live tests, mirroring the Python live coverage:
  - **Chained ergonomics** — ``uploadBytes`` -> create image ->
    ``submitAndWait`` -> terminal ``JobDetail``.
  - **SSE termination** — drains ``streamEvents()`` after the job
    reaches terminal, asserts drain time < 5s. A handler regression
    to an unterminated ``while True`` loop would surface as a
    timeout here rather than silently hanging consumers.
  - **submitAndStream** — drains the live SSE handle's ``events``
    iterator and resolves ``handle.result`` to the terminal
    ``JobDetail``.
  - **Parallel jobs** — submits two features-stage jobs against
    the same dataset in parallel, drains both SSE streams
    concurrently. Catches race conditions in the terminal-then-drain
    protocol that single-job tests miss (e.g. cross-job state
    bleed in ``_maybe_finalize_job`` or a SSE handler keyed on a
    process-level singleton).
  Skips when ``uv`` isn't available or when ``SFMAPI_LIVE_SKIP=1``.
  Symmetric to the Python live tests — proves the TS SDK actually
  composes against the running server, not just against
  ``msw``-stubbed transports.

### Regression guards
- ``tests/contract/test_e_generated_ergonomics.py`` carries cheap
  static + dynamic guards for every production-bug invariant the
  live-server end-to-end work has surfaced. Each guard's docstring
  documents the invariant it pins; grep for ``test_`` to enumerate.
  Don't delete a guard without first removing the invariant it
  protects.

### `InMemoryBlobStore` is a process-local singleton
- ``app/storage/blobs.py::get_blob_store()`` caches the in-memory
  backend instance because its bytes live in a per-instance dict.
  Constructing a fresh instance per call (as the FS / S3 backends
  do) means an upload via one call and a read via the worker land
  in different stores — bytes look "missing" even though they
  were correctly persisted. Use
  ``reset_memory_blob_store_for_tests()`` to drop the singleton
  between test cases that mutate ``settings.blob_backend``.
  ``regen_sdk.py`` snapshots and restores ``pyproject.toml``,
  ``README.md``, ``py.typed``, and ``_ergonomics.py`` across regens
  (the generator's ``--overwrite`` would otherwise delete them).
  When adding a new repo-owned file under ``sfmapi_client_gen/``,
  add it to ``PYTHON_METADATA_FILES`` in ``regen_sdk.py``. The TS
  ergonomics file lives outside the generator's overwrite path so
  it's safe by default.
- The TypeScript SDK exposes the generated layer at
  ``@sfmapi/client/generated`` and the raw paths/components at
  ``@sfmapi/client/generated/openapi``.
- Hand-rolled SDKs at ``clients/python/sfmapi_client/`` and
  ``clients/typescript/src/`` ship in parallel; contract tests in
  ``tests/contract/`` (and the TS ``test/generated.test.ts``) replay
  recorded fixtures through both layers so divergence fails CI
  immediately.
- Re-run ``scripts/regen_sdk.py`` after adding/changing any
  ``response_model``. Don't hand-edit files under
  ``sfmapi_client_gen/api/``, ``sfmapi_client_gen/models/``,
  or ``src/_generated/openapi.d.ts`` — they're overwritten
  on every regen.
- Async: `pytest-asyncio` with `asyncio_mode=auto`.
- HTTP tests use `httpx.AsyncClient(transport=ASGITransport(app))`.
- Storage / blob tests use `tmp_path` and a fresh in-memory SQLite engine.
- Fixtures in `tests/conftest.py`. Per-phase fixtures live in
  `tests/<unit|integration|e2e>/conftest.py`.

### Commits
- Conventional commits (`feat:`, `fix:`, `refactor:`, `test:`, `docs:`,
  `chore:`). Phase-N work tagged with `phase: N` in commit body.

## Build / Test / Dev Loop

```bash
# Setup (standalone — no Docker, no Redis, no Postgres)
uv venv
uv pip install -e ".[dev]"
cp .env.example .env             # defaults: SQLite + fs blobs + inline queue
uv run alembic upgrade head

# Run
uv run uvicorn app.main:app --reload

# Test (default — skips needs_backend / needs_postgres)
uv run pytest -q

# Dual-DB CI runs (must both pass)
SFMAPI_DB_URL=sqlite+aiosqlite:///./test.db uv run pytest -q
SFMAPI_DB_URL=postgresql+psycopg://sfm:sfm@localhost:5432/sfmapi_test \
  uv run pytest -q -m "not needs_backend"

# Lint + type
uv run ruff check .
uv run ruff format --check .
uv run mypy app
```

## Backend integration notes

- The web tier must work without any concrete backend installed (calls
  that need one return `501 CapabilityUnavailableError`). The
  `StubBackend` ships in this repo for tests + the
  `SFMAPI_EPHEMERAL=true` demo runtime; production deployments install
  a third-party backend package and call `register_backend()` at
  startup.
- Backends mutate fast (defaults change frequently). Cache invalidation
  uses the backend-defined `runtime_version_id` opaque string returned
  by `SfmBackend.runtime_versions()` and salted into every cache key.
- API NEVER opens the backend's working DB directly. All reads go via
  the backend's own methods; the API only serves sealed snapshots.
- `MappingInput.save/load` (`PCMAPIN\0` v1) is the canonical
  cross-stage + resume primitive — backends that support resume
  emit/consume this format.
- Pipeline callbacks drive `ProgressEvent` emission and snapshot
  triggers; backends register them via the methods on `SfmBackend`.
- `Reconstruction` is a run; produces N `SubModel` rows
  (`sparse/0`, `sparse/1`, ...). API reads are submodel-keyed.

## Anti-Patterns / Don'ts

- Don't import any engine library (pycolmap, torch, cv2, segment_anything,
  ...) from the web process. Ever. The
  `test_app_does_not_import_pycolmap_or_torch` unit test enforces this.
- Don't add a default backend to `app.adapters.registry` — sfmapi ships
  no engine on purpose.
- Don't read the backend's live working state from the API. Sealed
  snapshots only.
- Don't `SIGTERM` into a CUDA process for cancellation — corrupts context.
  Cooperative flag between phases; hard-kill = subprocess SIGKILL + worker
  restart.
- Don't store reconstructions in the DB. Paths + manifests only.
- Don't add Postgres-only features without a SQLite fallback.
- Don't put `tenant_id` in any URL path; carry via auth dep.
