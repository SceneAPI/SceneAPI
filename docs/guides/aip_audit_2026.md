# AIP Audit (May 2026)

Cross-language audit of sfmapi's wire surface against Google API
Improvement Proposals. Three review agents ran in parallel,
each scoped to a distinct AIP aspect:

1. **Resources + methods** (AIP-121, 122, 123, 130–136, 151, 158).
2. **Fields + compatibility** (AIP-140, 142, 148, 154, 162, 180, 185, 202, 216).
3. **Documentation + errors + auth** (AIP-191–194, 211, 217).

This document consolidates the findings, separates required fixes
from polish, and tracks which were shipped.

The audit is a snapshot. Future contract drift surfaces in the
existing regression-guard layer (`tests/contract/`) and the
decision register (`docs/guides/decisions.md`).

---

## Required fixes

### Shipped — RFC 7807 422 envelope (`L19`)

**Finding** (Agent 3): FastAPI's default `RequestValidationError`
emits `{"detail": [{loc, msg, type}, ...]}` — Pydantic shape, NOT
RFC 7807. Every other sfmapi error goes through `as_problem()` which
emits `{type, title, status, detail, instance}`. Inconsistency
defeats the SDK ergonomics' `raise_for_status` parser, which expects
RFC 7807.

**Shipped** (`app/main.py::request_validation_handler`): added a
FastAPI exception handler that wraps Pydantic field errors in the
RFC 7807 envelope. `detail` becomes a human summary (e.g.
``"body.name: Field required"``). The structured per-field errors
are preserved under a new `errors` key for machine-readable
consumers.

Recorded fixture (`tests/contract/fixtures/error_422_validation.json`)
re-recorded; regression guards updated in Python and TypeScript
suites.

### Shipped — JobAcceptedResponse stage-specific keys (`L22`)

**Finding** (Agents 1+2 converging): `JobAcceptedResponse` has
`extra="allow"` and stage endpoints attach stage-specific keys
(`target_recon_id`, `source_recon_ids`, `strategy`, `applied_sim3`,
`method`). The regression guard
`test_job_accepted_response_allows_extra_fields` pins this — it's
intentional, not accidental — but the SDK codegen sees the loose
envelope and emits `Any`/`Record<string, unknown>`, defeating
typed-SDK autocomplete.

**Shipped**: `JobAcceptedResponse` now declares every stage-specific
key as a typed optional field and no longer allows arbitrary extra
keys. Routes construct the typed envelope directly.

### Shipped — Image DELETE path parameter naming (`L33`)

**Finding** (Agent 1): `DELETE /v1/datasets/{dataset_id}/images/{name}`
uses `name` as the path parameter, but `name` on `ImageOut` is a
human-readable label, while `image_id` is the canonical key.

**Shipped**: added `DELETE /v1/images/{image_id}` as the canonical
ID-addressed route and kept `DELETE /v1/datasets/{dataset_id}/images/{name}`
as a compatibility alias for label-addressed clients.

---

## Polish (deferred — captured for posterity)

### Pagination naming — shipped (AIP-158)

After the user lifted the backwards-compat constraint (this is the
draft version; no released SDK consumers), `Page[T]` migrated to
AIP-158:

- `Page.next_cursor` → `Page.next_page_token`
- query param `?cursor=` → `?page_token=`
- query param `?limit=` → `?page_size=`
- service / repo helpers: `cursor: str | None` → `page_token: str | None`,
  `limit: int` → `page_size: int`

This unlocks codegen-helper auto-detection in `openapi-fetch`,
`openapi-python-client`, and the Google API generator family —
they recognize AIP-158 envelope shape and offer iterator helpers
without per-endpoint glue. Touched: `app/schemas/api/common.py`,
`app/api/v1/{projects,datasets,images,reconstructions}.py`,
`app/services/{project,image}_service.py`, both Python SDKs,
both TypeScript SDKs, the C++ header SDK, recorded fixtures,
and conformance tests.

`app/api/v1/ws_jobs.py::start_cursor` was NOT renamed because it's
SSE/WS event replay (Last-Event-ID style), a distinct concept from
AIP-158 list pagination.

### Timestamp `_at` vs AIP-142 `_time` (Agent 2)

sfmapi uses `created_at`, `updated_at`, `expires_at`, `started_at`,
`finished_at`. AIP-142 says `create_time`, `update_time`, etc.
**Decision**: keep `_at` — matches Python ecosystem convention
(Pydantic, SQLAlchemy, datetime). Document the choice as a sfmapi
standards exception.

### `name` field ambiguity (Agent 2)

`name` is a human-readable label on Project / Dataset / Image, NOT
a canonical resource name (which is the ULID `*_id`). AIP-148 wants
`display_name` for human labels. **Decision**: keep `name` — adding
`display_name` as a parallel field would just confuse consumers.
The field is documented and consistent across resources.

### Field masks on PATCH (Agent 2)

PATCH endpoints use Pydantic's `model_dump(exclude_unset=True)`,
not an explicit `update_mask`. AIP-134/161 prefer `update_mask`.
**Shipped** (`L33`): project and dataset PATCH endpoints accept an
optional AIP-161 `update_mask`. Omitting it preserves the legacy
implicit mask: fields present in the JSON body are updated and absent
fields are left unchanged.

### Schema versioning consolidation (Agent 2)

Three signals coexist: `schema_version` in response envelopes,
`spec_version` in `/spec`, `-v1` suffix in binary Content-Types.
**Decision**: keep all three. They serve different scopes (per-envelope
shape, whole-API spec, binary format). Documenting the layering
in `docs/guides/architecture.md` is the right action; collapsing
them would lose information.

### Type-tagged union discriminator docs (Agent 2)

`PipelineSpec` (discriminated on `kind`) and `ProgressEvent`
(discriminated on `kind`) lack docstrings explaining
unknown-discriminator handling. **Decision**: add docstrings; one
session of work, not blocking.

### SSE stream lifecycle docs (Agent 3)

`/v1/jobs/{id}/events` route docstring doesn't document
terminal-state stream closure, `Last-Event-ID` resume semantics, or
deletion-mid-stream behavior. **Decision**: add the docstring; the
behavior is locked (`L13`/`L14`) but the documentation lags.

### Admin endpoint warnings (Agent 3)

`/v1/admin/api-keys` routes lack docstrings explaining the
`auth_mode=none` development-only nature. **Decision**: add per-route
docstrings before real auth ships. Trivial.

### Capabilities endpoint docstring (Agent 3)

`GET /v1/capabilities` has no route docstring explaining the
dot-notated feature names + "absent means unsupported" rule.
**Decision**: add the docstring; one-line fix.

### Error type URI base — shipped

`SfmApiError.as_problem()` previously emitted
`type: "https://sfmapi/errors/..."` (placeholder authority); the
`/spec` endpoint previously advertised `https://sfmapi.dev/spec`
(domain not owned). Both standardized on the canonical GitHub Pages
domain `https://sfmapi.github.io/...`:

- `SfmApiError.as_problem()` → `https://sfmapi.github.io/errors/<slug>`
- `RequestValidationError` 422 handler → same pattern
- `GET /spec` `spec_url` → `https://sfmapi.github.io/spec` (settable
  via `SFMAPI_SPEC_URL`)
- `SFMAPI-SPEC.md` documentation example aligned

Recorded fixtures (`error_422_validation.json`, `spec.json`)
re-recorded; SDK contract assertions kept loose (URI shape, not exact
string) so deployments can override the spec_url without breaking
SDK tests.

---

## Coverage matrix (cross-agent)

| Subsystem | Resources/Methods | Fields/Compat | Docs/Errors/Auth |
|---|---|---|---|
| projects | reviewed | reviewed | reviewed |
| datasets | reviewed (dual-path noted) | reviewed | reviewed |
| images | reviewed (DELETE param noted) | reviewed | reviewed |
| uploads | reviewed | reviewed | reviewed |
| jobs | reviewed (LRO shape noted) | reviewed | reviewed (SSE docs gap) |
| reconstructions | reviewed (no PATCH) | reviewed | reviewed |
| pipelines | reviewed | n/a | reviewed |
| sfm_stages | reviewed | n/a | reviewed |
| localize | reviewed | n/a | reviewed |
| oneshot | reviewed (AIP-1 exception) | reviewed (schema_version split) | reviewed |
| capabilities | n/a | reviewed (schema_version) | reviewed (docs gap) |
| common | n/a | reviewed (Page, TimestampedModel) | n/a |
| scene | n/a | reviewed (no AIP issues) | n/a |
| pipeline_spec | n/a | reviewed (type-tagged unions) | n/a |
| binary formats | n/a | reviewed (Content-Type version) | n/a |
| admin | reviewed | n/a | reviewed (docstring gap) |
| health | n/a | n/a | reviewed |

---

## What didn't surface as a real issue

The agents flagged these but evidence doesn't support fixing them:

- **Resource hierarchy "dual-path"** (Agent 1): datasets ARE primarily
  nested under projects; the top-level `/v1/datasets/{did}/images`
  reads are convenience routes for image operations that don't need
  the project context. Documented intent.
- **Custom methods `:render_cubemap`, `:merge`, `:batch`**: Agent 1
  confirmed all are appropriate AIP-136 custom methods.
- **Reconstruction PATCH missing**: reconstructions are read-only
  artifacts (sealed snapshots are the only mutable state). Adding
  PATCH would imply mutability that doesn't exist.
- **AIP-211 auth ordering**: agent flagged that 403 vs 404 leaks
  tenant existence. True, but real auth doesn't ship yet
  (`auth_mode=none` default). Will revisit when `P3` (RLS-on-Postgres)
  is approved.

---

## Decision register impact

- New row `L19` added: RFC 7807 422 validation envelope is canonical.
- New row `L20` added: AIP-158 pagination shape (`page_token` /
  `page_size` / `next_page_token`).
- `L11` (response_model required) reaffirmed by audit.
- No locked decisions invalidated; no cancelled items reopened.
- `P1`–`P5` proposals unchanged (resume primitives, S3 snapshots,
  RLS, oneshot phase c, streaming SLAM).

**Counts after audit**: 20 locked + 6 cancelled + 5 proposed = 31.

## Backwards-compat constraint lifted (2026-05-04)

The user explicitly waived the "stable in place" constraint for the
draft project — there are no released SDK consumers yet. This
unblocked the pagination rename above; it is a reusable lever for
any future renames where the only blocker is "would break recorded
fixtures." Future renames considered under this lever should still
be motivated by real wire-correctness wins, not aesthetic
preference (e.g., `*_at` → `*_time` was deferred not because of
compat but because Python ecosystem convention favors `_at`).

## /auto-tune Round 1+2+3 (2026-05-05) — shipped

Three-agent re-audit produced a fresh REQUIRED list; all items
shipped. The full set:

### Round 1 — schema/wire correctness
1. **Status enums** (`L21`): `JobStatus`/`TaskStatus`/`UploadState`/
   `ReconstructionStatus` typed as `Literal[...]` on the wire (was
   bare `str`). SDK codegen now emits string-union types; consumers
   get compile-time errors on typo'd terminal-state literals.
2. **JobAcceptedResponse cleanup** (`L22`): dropped `extra="allow"`,
   added typed Optional fields for every stage-specific key
   (`target_recon_id`, `source_recon_ids`, `strategy`, `applied_sim3`,
   `method`); routes no longer dict-spread untyped extras into the
   202 envelope. Regression guard rewritten to assert the typed
   shape and survive the round-trip.
3. **503 → 501 capability mismatch**: server-side
   `CapabilityUnavailableError`/`PycolmapUnavailableError` already
   emitted 501; both Python and TypeScript SDK ergonomics shims
   were mapping 503 → `PycolmapUnavailableError`. Aligned to 501,
   updated `app/adapters/sam_adapter.py` and SPEC error class
   table accordingly.
4. **MatchesSpec retired**: deleted the legacy combined shape from
   `pipeline_spec.py`; `/v1/datasets/{did}/matches` now takes
   `{pairs: PairsSpec, matcher: MatcherSpec}` (AIP-202 — one
   concept per type). Worker `match.py`, hand-rolled SDKs (Python
   sync + async + TS + C++), bench harness with legacy-YAML
   translation, and SPEC.md/api.md docs all updated.
5. **ProblemResponse extended**: added `errors[]`, `capability`,
   `retry_after` (AIP-193) so SDK codegen produces typed access
   to the structured payload the server emits on 422 / 501 / 429.
6. **SPEC.md §3.6 + §3.4 doc-fixes**: pagination doc still said
   `next_cursor`/`?cursor=`; error class table mapped 503 to
   pycolmap_unavailable. Both aligned to the live wire.

### Round 2 — verb renames (AIP-136)
7. **`DELETE /v1/jobs/{id}` → `POST :cancel`** (`L23`): cancellation
   is a side-effecting operation with cooperative-vs-force flag,
   not a deletion. SDKs renamed to send `POST :cancel`.
8. **`POST /jobs/{id}/resume` → `:resume`**: colon-verb consistency.
9. **`POST :batch` → `:batchCreate`** (AIP-231): the batch shape
   wasn't a `Page[T]` — it was a list of created resources. New
   `BatchCreateImagesRequest` (with `requests:`) +
   `BatchCreateImagesResponse` (with `images:`) replace
   `ImageBatchCreate` + `Page[ImageOut]`.
10. **`POST /uploads/{id}/finalize` → `:finalize`**: colon-verb
    consistency.

### Round 3 — propagation cleanup
11. **TS hand-rolled SDK**: dropped dead `MatchesSpec` re-export +
    obsolete `ImageBatchCreate` type; replaced with
    `BatchCreateImagesRequest`/`Response`. Test suite updated to
    new `submitMatches` signature. `tsc --noEmit` now clean.
12. **bench harness**: replaced `MatchesSpec` import + usage with
    `PairsSpec`/`MatcherSpec`; added legacy-YAML translation so
    existing `dataset.yml` files with `matches.mode` still load
    cleanly.
13. **SPEC.md + docs/reference/api.md**: swept all `MatchesSpec`,
    `:batch`, `DELETE /v1/jobs`, `/finalize`, `/resume` doc
    references to the new wire shapes.
14. **Design-doc cleanup**: `oneshot_streaming_proposal.md`,
    `resume_unification_proposal.md`, `jobs_and_progress.md`,
    `phase_0_skeleton.md`, `phase_5_resume_tenancy_s3_obs.md`
    updated to use the colon-verb routes in narrative text.

### What remains POLISH (deferred)

- Timestamp `_at` → `_time` (intentional Python-ecosystem exception)
- `name` → `display_name` (cancelled — would create parallel-field
  confusion)
- Idempotency-Key on Job-submit POSTs (~3h, needs dedupe table)
- 401 vs 403 split for missing-vs-rejected auth (auth_mode=none
  default makes this dormant)

Re-audit Round 4 confirmed ZERO REQUIRED issues.

## /auto-tune Round 5 (2026-05-05) — polish until clean

Three-agent polish-focused re-audit. Items shipped:

### Wire-completeness fixes (`L24`)
1. **Pagination implementation gap closed**: `list_datasets` +
   `list_submodels` now do real AIP-158 keyset pagination
   (`app/services/{dataset,reconstruction}_service.py`); routes wire
   `page_token` / `page_size` straight through and emit a real
   `next_page_token`. Previously these accepted the params and
   silently ignored them — `next_page_token` was always `null`.
2. **`GET /v1/jobs` list endpoint** added (AIP-158 + closed-set
   `?status=` filter). Most-recent-first via descending `job_id`
   keyset; `JobStatus` `Literal[...]` enum gates the filter so
   typo'd values 422 cleanly. Replaces the previous "GET by id only"
   gap that forced consumers to remember every job they'd submitted.

### Typed-replacement fixes (replaces bare `dict` with named
schemas — AIP-203)
3. **`JobAcceptedResponse.applied_sim3`**: `dict | None` →
   `Sim3 | None` (`Sim3` already existed in `app/schemas/api/scene.py`).
   Localize `:georegister` callers now get typed access to
   `rotation` / `translation` / `scale` instead of `Record<string, unknown>`.
4. **`ReconstructionOut.spec`**: `dict` → `PipelineSpec` (the
   discriminated union on `kind`). SDK codegen produces a typed
   tagged-union accessor; consumers can `match recon.spec.kind:`
   without manual coercion.
5. **`ImageObservationRow` / `PointObservationRow`**: replaces
   `list[dict[str, Any]]` on `ImageObservationsResponse` and
   `PointVisibilityResponse`. Field set is documented and stable
   (`{point3d_id|image_id, kp_idx, x, y, error}`).
6. **`UploadEntrySpec`**: replaces `list[dict]` on
   `UploadSourceSpec.entries`; binds `name` + `blob_sha` validation
   to the request schema instead of dict-shape duck typing in
   `dataset_service.create_image_source`.
7. **`SourceSpec` discriminator wrapper**: tagged as
   `Annotated[UploadSourceSpec | LocalSourceSpec | S3SourceSpec,
   Field(discriminator="kind")]` — explicit Pydantic discriminator
   so OpenAPI emits a tagged-union schema (matches `PipelineSpec`,
   `ProgressEvent`).

### `updated_at` on resources that track it (AIP-142 partial)
8. **`ProjectOut.updated_at`** + **`DatasetOut.updated_at`**: ORM
   columns already existed (`Project.updated_at`, `Dataset.updated_at`)
   but the wire schemas only exposed `created_at`. Now exposed; SDK
   consumers can ETag-poll for changes. Job/Submodel left alone —
   they're append-only resources without an `updated_at` column.

### Documentation completeness (`L25`)
9. **Route docstrings**: every `@router.<method>` decorator across
   `app/api/v1/*.py` has a docstring covering the body, response
   shape, edge cases, and AIP citations. Touched 36 routes across
   admin / capabilities / version / metrics / jobs / projects /
   datasets / images / sfm_stages / uploads / reconstructions /
   pipelines / ws_jobs. Auto-generated OpenAPI doc (Redoc / Swagger
   UI / SDK codegen) now reflects the full contract instead of bare
   path strings.
10. **Discriminated-union docstrings**: `PipelineSpec`,
    `ProgressEvent`, `SourceSpec` modules now document the
    discriminator field, the variant list, the forward-compat rule
    ("treat unknown discriminators as unsupported"), and the
    capability-flag mapping.
11. **Resource model docstrings**: every `*Out` class
    (`JobOut`/`TaskOut`/`JobDetail`, `ReconstructionOut`/`SubModelOut`,
    `DatasetOut`, the `*Sim3`/`Rigid3`/etc. scene types) carries a
    class-level docstring on rollup semantics, terminal vocab,
    cursor behavior, etc.
12. **Error catalog docstrings**: every class in `app/core/errors.py`
    documents the HTTP status, when to raise it vs a sibling, and
    which extras land in the problem+json envelope (e.g.
    `capability` on 501, `retry_after` on 429).

### Observability (AIP-155)
13. **`RequestIdMiddleware`**: reads `X-Request-ID` (or generates a
    fresh ULID), echoes it back, and binds it to `structlog`
    contextvars for the lifetime of the request. Wired in
    `app/main.py` ahead of the router; the header rides CORS via
    `expose_headers`. Lets clients stitch their logs to ours
    deterministically.

### What truly remains POLISH (deferred — none of these are wire bugs)

- Per-field `Field(..., description=...)` on every response model
  (~150 fields). Class-level docstrings already orient the reader;
  field-level descriptions matter mostly for IDE-tooltip codegen.
  Real ROI is low until we ship a hosted Redoc.
- Timestamp `_at` → `_time` (intentional Python-ecosystem exception).
- `name` → `display_name` (cancelled per audit decision; would create
  parallel-field confusion).
- Idempotency-Key on Job-submit POSTs (~3h, needs dedupe table).
- 401 vs 403 split (auth dormant; explicit revisit when real auth
  ships in P3).
- LRO `metadata`/`observed_state`/`reconciling` — AIP-151 nice-to-haves
  that don't fit the imperative-Job model sfmapi uses (Jobs aren't
  reconciled resources).
- Submodel `summary`/`rigidity` typed shapes — diagnostics endpoint
  (`Phase 2.9`) hasn't shipped a producer yet; locking the wire
  shape now would just guess.
- `outputs_ref` discriminated by `Task.kind` — the field is overloaded
  with `__inputs`/`__spec` resume keys (architectural mix of
  pre-execution checkpoint state + post-execution result). Real fix
  is splitting `task_state_json` from `outputs_ref_json`, which is
  proposal P1 territory.
- Custom-verb symmetry on `/localize`, `/mesh`, `/dense`,
  `/georegister` — currently slash-suffixed sub-resource paths;
  AIP-136 strict reading would prefer `:localize` etc. Coherent ask
  but too soon after L23 verb churn.
- `Reconstruction.updated_at`, `Job.updated_at` — would require an
  Alembic migration (no column exists). Worth doing alongside other
  schema work in P5; not urgent.

Round 5 leaves the API at a stable polish floor: every remaining
item is either an intentional exception, larger work that needs
explicit user direction, or a cosmetic improvement with low ROI.

## Smells / anti-patterns audit (2026-05-06) — closed

A `/optimization-pack:review-smells-and-antipatterns` repo-wide
audit produced 10 findings: 4 code smells, 3 design smells, 3
anti-patterns. Closure summary (per the plan in
`C:\Users\opsiclear\.claude\plans\wobbly-dancing-milner.md`):

### Phase A — cheap extractions (3 of 4 shipped)
- **A1 (gap #2) — shipped**: 8x `JobAcceptedResponse → JSONResponse(202, Location)`
  inline blocks consolidated into `app/schemas/api/jobs.py::accepted_response`.
  Routes call `accepted_response(JobAcceptedResponse(...))` instead of
  re-implementing the envelope. Localize's `_accepted` is now a
  one-line alias.
- **A2 (gap #4) — discarded after calibration**: the proposed
  `links_for(row)` registry would lengthen every call site without
  removing the domain-specific `_<r>_links` builders. Per-router
  `_to_out` wrapper is 2 lines and clear; the smell was Severity:
  low, the fix would be worse than the smell.
- **A3 (gap #3) — shipped**: `dataset_service.get_dataset(... project_id=)`
  enforces the project boundary inside the service. The 3 inline
  `if d.project_id != project_id:` checks in `app/api/v1/datasets.py`
  are gone.
- **A4 (gap #10) — shipped**: `_StageReqBase` flipped from
  `extra="ignore"` to `extra="forbid"`. Typo'd field names now 422
  instead of silently shipping defaults to the worker.

### Phase B — `outputs_ref_json` split (`L27`, all shipped)
- **B1**: `task_state_json` column added with Alembic migration
  `0007`.
- **B2**: producer at `app/services/job_service.py::materialize_dag`
  switched from `t.outputs_ref_json = dict(n.metadata)` to
  `t.task_state_json = dict(n.metadata)`.
- **B3 (gap #1)**: new `app/workers/_task_io.py` exposing
  `read_state(task)` / `read_inputs` / `read_spec` / `read_extra`.
  19 worker files swept — every `(task.outputs_ref_json or {}).get("__inputs")`
  duplication retired.
- **B4**: magic keys `__inputs` / `__spec` renamed to `inputs` /
  `spec` everywhere (DAG metadata, worker reads, test fixtures).
- **B5**: SDKs regenerated; `task_state_json` confirmed not on the
  wire (backend-only column). Worker results still surface as
  `TaskOut.outputs_ref` exactly as before.

### Phase C — remaining design fixes (both shipped)
- **C1 (gap #7)**: `ImageObservation` / `PointObservation` storage
  dataclasses retired; `app/storage/observations.py` re-exports
  `ImageObservationRow` / `PointObservationRow` from the wire schema
  (`app/schemas/api/reconstructions.py`). Writers dump via
  `model_dump(exclude_none=True)`.
- **C2 (gap #6, `L28`)**: `BlobStore.is_singleton: bool` declared on
  each backend; `get_blob_store()` caches via
  `_INSTANCES: dict[type, BlobStore]`. The `if cls is InMemoryBlobStore`
  class-conditional dispatch is gone. The `L15` regression guard
  still pins the singleton invariant.

### Phase D — tracked, not shipped
- **D1 (gap #8)**: hand-rolled SDK removal milestone documented in
  `L12` and the deprecation warning at
  `../sfmapi-sdk/python/sfmapi_client/__init__.py:87`: target removal at
  `0.1.0`. No code deletion this loop.
- **D2 (gap #9, `L29`)**: `ColmapModBackend` god-class split
  formally deferred. Decision register entry documents the calibration:
  pycolmap's surface is genuinely broad (23 stage methods); a
  composition split would add navigation cost without closing a
  real defect.

### Result
- **All 10 gaps either closed or formally tracked.**
- 449 Python tests + 84 TS tests pass.
- `git grep '__inputs\|__spec' app/` returns zero hits.
- `git grep 'extra="ignore"' app/api/` is empty.
- `task_state_json` is backend-only (verified via OpenAPI regen).

Loop closes.

## Smells / anti-patterns audit round 2 (2026-05-06) — closed

A re-audit on the post-closure codebase surfaced 5 follow-on
findings; all closed.

1. **Layering inversion (high)**: Phase A1's `accepted_response()`
   helper landed in `app/schemas/api/jobs.py`, pulling FastAPI
   imports into the schemas layer. **Fix**: moved to a new
   `app/api/v1/_helpers.py`; schemas/jobs.py is back to Pydantic-only.
   7 router import sites updated.
2. **Dead code (low)**: `read_spec()` in `_task_io.py` had no
   callers. **Fix**: deleted.
3. **Vestigial alias (low)**: `_accepted = accepted_response` in
   `localize.py` left in place to avoid touching 5 call sites.
   **Fix**: inlined the 5 call sites; alias dropped.
4. **Phase C1 regression (medium)**: `app/storage/observations.py`
   had been silently restored to its pre-C1 dataclass form (a
   linter / formatter likely dropped the file change). The audit
   doc claimed the fix shipped; the code didn't reflect it.
   **Fix**: re-shipped the wire/storage fold cleanly — storage now
   re-exports `ImageObservationRow` / `PointObservationRow` from the
   wire schema (no rename); 4 caller sites (snapshot_emit + 2 test
   files + reconstructions API) updated to the canonical names.
5. **God-service (medium, `L30`)**: `sfm_stage_service.py` had 13
   `submit_*` methods sharing an identical 8-line tail
   (`_stage_node` + `submit_job_dag` boilerplate). **Fix**:
   extracted `_submit_single_stage()` helper; all 13 callers route
   through it. Also extracted `_reconstruction_paths(tenant_id, r)`
   helper for the `(rec_root, sparse_dir)` pattern that 5 callers
   reproduced. File is now 774 LOC (was 722 — slightly larger from
   helper docstrings + decision register entries) but every
   cross-cutting change to job submission lands in one place
   instead of 13.

All 5 round-2 fixes verified: 449 Python + 84 TS passing
(occasional Windows port-bind flake on the TS live-server suite —
known infra noise unrelated to the code).
