# Lean Audit 2026-07 — Remediation Plan & Checklist

Full-workspace review (core `sfmapi`, 12 plugin repos, `sfmapi-sdk`,
`sfmapi-cpp`, `sfmapi-bench`) conducted 2026-07-15. Goal stated by the
owner: **lean, efficient, clean**. This document records the validated
findings, the decisions the plan is gated on, and a phased checklist.

Effort scale: **S** ≤ 1 h · **M** ≤ 1 day · **L** = multi-day.
Items marked **⚠ user-confirm** are destructive or externally visible
and must not be executed without explicit sign-off.

---

## 0. Validation summary

Every finding below was re-verified against the working tree on
2026-07-15 (not taken from reviewer notes on faith). Corrections made
during validation:

- Keyset-pagination duplication is **14 sites across 10 files**
  (originally reported as ~12) — slightly worse than reported.
- `needs_pycolmap` appears in 5 files only because of `__pycache__`
  artifacts; real source references are **2 docstring mentions, zero
  decorations** — the dead-CI-lane finding stands.
- Suite state at audit time: **1101 passed / 3 failed**
  (`tests/e2e/test_datasets_api.py::test_core_request_bodies_reject_unknown_fields`,
  `tests/e2e/test_radiance_api.py::test_radiance_results_store_only_public_provider_outputs`,
  `tests/unit/test_plugin_server.py::test_capabilities_endpoint_validates_backend_capability_ids`
  — the last one because `/capabilities` now returns 200 where the
  guard expects a 500 on invalid capability ids). Ruff: 43 lint
  errors, 75 files unformatted (same ruff 0.15.12 as the lock). 145
  uncommitted files (28 half-staged).

Key confirmed measurements:

| Fact | Evidence |
|---|---|
| API surface 123 paths / 136 ops / 215 schemas at 0.0.1 | parsed `openapi.json` |
| Oneshot handlers run engine work on the event loop | `app/api/v1/oneshot.py:78,133` call services synchronously from `async def` |
| numpy imported at web startup | `api/v1/similarity.py:22` → `services/similarity_service.py:26-27` → `storage/vlad.py:29` (module level) |
| Dependency-readiness logic ×3 with vocab drift | `orchestrator/scheduler.py:23` (succeeded only) vs `workers/dispatcher.py:494-513` and `orchestrator/janitor.py:92` (succeeded+skipped) |
| Janitor full-table scans, no retention/GC anywhere | `janitor.py:83,105` `select(Task)` unfiltered; grep for retention/prune empty |
| Task lacks composite (status, lease_expires_at) index | `db/models.py:296-299` two single-column indexes |
| Services→adapters layering rule broken ×6 (one private import) | `radiance_service.py:235` imports `_radiance_train_option_schema`; 5 more files |
| BA mode→capability map duplicated across web/worker | `sfm_stage_service.py:983-987` vs `workers/tasks/ba.py:24-29` |
| `detect_capabilities` swallows 4 probe failures silently | `core/capabilities.py` 4× `except Exception: pass` |
| `core/colmap_db.py` (469 LOC) unused by runtime | zero imports from `app/` |
| WebSocket fully shipped vs locked L9 "SSE-only" | `ws_jobs.py` + `main.py:343` vs `decisions.md` L9/P5 |
| depth/normal binary formats not on the wire | 0 hits in `openapi.json`; only a stale `depth_map_binary.cpython-312.pyc`; SDK parsers + CLAUDE.md still document them |
| Radiance trainer copy-paste | `trainer.py` brush↔lfs differ by **10 / 1143 lines**; each copy carries all providers' `_train_*` functions |
| Core plugin-server kit unadopted | `app/plugin_server.py` `PROTOCOL_VERSION="1.1"`; all radiance `protocol.py` hand-rolled at `"1.0"`; `build_plugin_server` imports in plugin repos: **0** |
| Radiance plugins missing the `sfmapi` dependency | `sfmapi_brush/pyproject.toml` deps = fastapi/uvicorn/pydantic only, yet `plugin.py` imports `sfmapi.backends` |
| COLMAP trio duplication | `model.py` md5-identical ×3; `pycolmap_backend.py` forked in colmap + pycolmap repos |
| TS ships two client stacks, hand-rolled is primary | `package.json` root export → hand-rolled `dist/index.*` (src/client.ts 1,400 LOC); generated `client.ts` = 178-line stub |
| Deprecated Python SDK load-bearing | `sfmapi/bench/{cli,harness,metrics}.py` import `sfmapi_client`; `sfmapi_client_gen/_ergonomics.py:31` roots its error class in the deprecated package |
| `sfmapi-cpp` is a second server | CMake: `find_package(Drogon)`, `add_executable(sfmapi_cpp_server)`; parity+plan apparatus larger than the product; unreferenced by other repos |
| CI gaps | lint covers `app tests` only; `real-engine` lane filters on unregistered `needs_pycolmap` (0 tests) with placeholder install steps |
| Hygiene | `uploads/` not gitignored; helm `values.yaml:127` `password: changeme`, no `existingSecret`/`secretKeyRef`; ~4.1 GB unreferenced clones at workspace root; `.git.broken-worktree` markers in all 3 colmap repos; orphaned `0.2.0` tarballs in local `dist/` |
| Wheel ships top-level `app` package | `pyproject.toml` `packages = ["app", "sfmapi", "sfm_hub"]` |

Explicitly **not** re-litigated (respecting `decisions.md` Cancelled
items): S3 cache unification (C1), settings submodels (C2), C++ live
test (C3), checkpoint unification (C4), snapshot-on-S3 alternatives
(C5/C6).

---

## Phase 0 — Decision gates (blockers for later phases)

These are owner calls. Each gets a decision-register row once made.

- [ ] **D1 — Define the v0 kernel.** Recommendation: kernel =
  projects → uploads → datasets → images → SfM stages → jobs/events →
  reconstructions → artifacts + backend discovery. Fence
  **admin routing profiles (5 ops of the 16 admin ops), dataflow/
  processors (6 ops), similarity** behind a "preview/experimental"
  conformance level (the spec already defines conformance levels) or
  a settings flag that drops them from the default OpenAPI. Radiance
  stays (it has 5 working plugins) but as a labeled extension.
  Target: ~100 ops in the default contract. **Gates 7.1.**
- [ ] **D2 — Decide `sfmapi-cpp`'s status.** Recommendation: freeze
  (archive branch, README pointer, stop parity CI) until the Python
  surface stops moving; the 25.8k-line parity harness re-verifies a
  moving target. Alternative: declare it the product and freeze the
  Python surface instead. **Gates 7.3.**
- [ ] **D3 — Repo topology.** Recommendation: merge the 5 radiance
  repos into one `sfmapi_radiance` (5 providers, per-provider extras),
  and the 3 COLMAP repos into one `sfmapi_colmap` (3 providers).
  17 → 11 repos without a monorepo migration. **Gates 4.3/4.4.**
- [ ] **D4 — Top-level `app` package.** Recommendation: fold `app/`
  into `sfmapi/` (e.g. `sfmapi._app` or `sfmapi.server`) **before any
  external consumer exists**; shipping a wheel that owns the global
  `app` name is a permanent collision hazard and is the root cause of
  good shared code (`app/plugin_server.py`) being invisible to
  plugins. Large mechanical rename — schedule deliberately. **Gates 7.4.**

---

## Phase 1 — Stabilize the tree and make CI honest (do first)

- [ ] 1.1 **Land or shelve the 145-file WIP** as reviewable commits;
  stop the "land WIP" mega-commit pattern (it defeats the repo's own
  contract-test machinery). (M)
- [ ] 1.2 **Green the 3 failing tests** (unknown-field rejection on
  dataset bodies; radiance public-provider outputs; plugin-server
  capability-id validation — decide whether the new 200 behavior or
  the pinned 500 is correct and update the loser). (M)
- [ ] 1.3 **Ruff clean**: `ruff check --fix` + `ruff format` across
  `app sfmapi sfm_hub tests scripts`; then fix the **CI lint command**
  (`.github/workflows/ci.yml:37,39`) to cover all four packages, per
  CLAUDE.md. Acceptance: CI lint job runs the same command as docs. (S)
- [ ] 1.4 **Delete or finish the `real-engine` CI lane**
  (`ci.yml:108-149`). If kept: register the marker, decorate real
  tests, implement the install steps. If not: delete the job and the
  `needs_pycolmap` filter mentions (`ci.yml:60,106`), and drop the
  unused `needs_backend` marker or start using it. (S–M)
- [ ] 1.5 Add `uploads/` to `.gitignore` (sibling of `workspaces/`). (S)
- [ ] 1.6 **⚠ user-confirm** Delete workspace-root clutter: `brush/`
  (3.6 GB unmodified upstream clone), `SphereSfM_original/` (491 MB),
  `_archive/` (436 KB — contains the old `3dgsapi.bundle`; keep the
  bundle somewhere if the history matters). Also: `.tmp*` scratch
  dirs in `sfmapi/`, `.git.broken-worktree` markers in the 3 colmap
  repos, orphaned `0.2.0` tarballs in plugin `dist/` dirs, stale
  `outputs/` run dumps in `sfmapi_hloc` / `sfmapi_gsplat`. (S)
- [ ] 1.7 **Helm secrets**: support `existingSecret`/`secretKeyRef`
  for DB/Redis/API keys in `web-deployment.yaml`; remove the
  `changeme` default (fail template render if unset instead). (M)
- [ ] 1.8 **Radiance plugin packaging**: add `sfmapi>=0.0.1,<0.1`
  dependency + `[tool.uv.sources]` to brush/gsplat/fastergs/lfs/
  spirulae; align `requires-python` (>=3.12,<3.13) and version with
  the rest of the fleet; fix `sfmapi_brush` pyproject (>=3.10) vs its
  own manifest (>=3.12) contradiction. Acceptance: fresh-venv
  `pip install` of each plugin can import its entry point. (S)
- [ ] 1.9 Add minimal CI to plugins that have none (at least
  `sfmapi_brush`: lint + unit tests). (S)

## Phase 2 — Core correctness & efficiency (small diffs, high value)

- [ ] 2.1 **Oneshot off the event loop**: wrap
  `oneshot_service.extract_features_oneshot` / `localize_oneshot`
  calls in `anyio.to_thread.run_sync` (`api/v1/oneshot.py:78,133`).
  Acceptance: a slow oneshot request no longer blocks `/healthz`. (S)
- [ ] 2.2 **Lazy numpy**: move `storage/vlad.py:29` import into the
  functions that use it; extend the existing import-guard test
  (`test_app_does_not_import_pycolmap_or_torch`) to also assert
  `numpy` is absent after `create_app()`. (S)
- [ ] 2.3 **Single-source dependency readiness**: one function + one
  vocabulary (`{succeeded, skipped}`) shared by
  `scheduler._dependency_ready`, `dispatcher._dependency_state_from_statuses`,
  and the janitor. Fixes the real bug where a task depending on a
  cache-`skipped` upstream isn't enqueued at submit and waits for a
  janitor sweep. Add a regression test for submit-time enqueue with a
  skipped dependency. (M)
- [ ] 2.4 **Janitor queries**: filter in SQL (`status='pending'` for
  readiness; `status='running' AND lease_expires_at < now` already
  filtered) instead of `select(Task)` twice per tick; scope
  `propagate_terminal_dependencies` to jobs with non-terminal tasks.
  Add composite `Index("ix_task_status_lease", "status",
  "lease_expires_at")` (migration 0012, dialect-neutral). (M)
- [ ] 2.5 **Retention/GC policy**: settings-driven sweep for terminal
  Jobs/Tasks/events older than N days (+ blob refcount-zero sweep) as
  a janitor stage; without it the Task table and `events.jsonl` grow
  forever and every sweep slows linearly. Write the short proposal doc
  per the decision-flow, then implement. (M)
- [ ] 2.6 **Process-level queue reuse**: cache the `Queue` (Redis
  pool) per process; close it in lifespan shutdown; stop per-call
  `get_queue()`/`close()` churn in `dispatcher._enqueue_task_ids` and
  the janitor. (S)
- [ ] 2.7 **Stop swallowing probe errors**: add `log.debug` (or
  `warning`) to the 4 `except Exception: pass` sites in
  `core/capabilities.py` and the bare except in `ws_jobs.py:167`. (S)
- [ ] 2.8 Move the mid-service `session.commit()`
  (`artifact_conversion_service.py:472`) to the caller so services
  uniformly flush-not-commit. (S)
- [ ] 2.9 **BA map single source**: define mode→capability once in
  `schemas/pipeline_spec.py`; import from both
  `sfm_stage_service.py:983` and `workers/tasks/ba.py:24`. (S)
- [ ] 2.10 **Engine-neutral error**: introduce
  `BackendUnavailableError`; keep `PycolmapUnavailable` as the wire
  `error_class` alias until 0.1.0 (it is serialized state — note in
  the changelog). Remove pycolmap special-casing from
  `dispatcher.py:756`. (S–M)
- [ ] 2.11 Fix the stale `bridge/bridge_worker.py` reference in
  `orchestrator/queue.py` docstring (points at another repo). (S)

## Phase 3 — Core refactors (mechanical; one PR each)

- [ ] 3.1 **`paginate_keyset()` helper** (session, stmt, page_size,
  page_token, pk column) and migrate the **14 sites / 10 files**.
  Acceptance: no `page_size + 1` literal outside the helper. (M)
- [ ] 3.2 **Dispatcher decomposition**: extract `_finalize_task(...)`
  for the ~7 repeated terminal-transition blocks; register per-kind
  lifecycle hooks (recon/radiance status rollups, success side
  effects) alongside `@task_handler` instead of hardwiring kind
  checks; move `_apply_derived_dataset_outputs` (~250 lines) into
  `dataset_service.register_derived_dataset()`. Target:
  `dispatcher.py` ≤ 400 lines with no domain imports beyond services. (L)
- [ ] 3.3 **Descriptor-registry base** for the `backend_config` /
  `backend_actions` / `backend_artifacts` triplet (~1,687 LOC →
  shared `_normalize/_dedupe/_link` + generic
  list/has/get/violations/assert surface). (L)
- [ ] 3.4 **`sfm_stage_service` diet**: table-driven recon-stage
  submits (`:990-1119`, ~8 near-identical wrappers → one dict), then
  split the 1,491-line module (dataset stages / recon stages /
  recipes). (M)
- [ ] 3.5 **Layering rule**: promote the needed adapter helpers to a
  public seam (e.g. move option-schema builders into
  `app/schemas/backend_options.py`) so the 6 services stop importing
  `app.adapters.*`; make `_radiance_train_option_schema` public
  wherever it lands; add an import-linter/test guard so the rule is
  enforced, or consciously amend CLAUDE.md instead. (M)
- [ ] 3.6 Relocate `core/colmap_db.py` (469 LOC, runtime-unused) to
  the contracts surface it actually serves (e.g. `sfm_hub/contracts/`
  or `sfmapi.contracts`) and export it publicly if plugins are meant
  to consume it. (S)
- [ ] 3.7 Type `BatchLocalizationBackend.localize_batch(**kwargs)`
  properly (`adapters/backend.py:323`). (S)
- [ ] 3.8 Split `sfm_hub/models.py` (70 KB) into modules; it is now
  lint-covered after 1.3. (M)

## Phase 4 — Plugin ecosystem consolidation (after D3)

- [ ] 4.1 **Publish the plugin-service kit**: re-export
  `build_plugin_server`, protocol models, `PROTOCOL_VERSION` under
  `sfmapi.plugin_service`. Plugins only adopt public `sfmapi.*`
  surfaces — this is why adoption is currently zero. (S)
- [ ] 4.2 **Adopt the kit** in vismatch + the 5 radiance plugins:
  delete hand-rolled `server.py`/`protocol.py` pairs; everyone speaks
  protocol 1.1; drop their redundant fastapi/uvicorn/pydantic deps. (M)
- [ ] 4.3 **Radiance 5→1** (per D3): one `sfmapi_radiance` repo; the
  1,143-line multi-provider `trainer.py` becomes the engine, each
  provider a ~40-line config (constants + manifest + entry point);
  gsplat keeps its genuinely different CUDA trainer as a module.
  Removes ~3,400 duplicated lines and the pprint-vs-handwritten
  manifest drift. (L)
- [ ] 4.4 **COLMAP 3→1** (per D3): one repo, three providers via the
  documented `Plugin.register_hook` multi-backend pattern; unify the
  two forked `pycolmap_backend.py` (1,032 drifted lines — diff and
  reconcile deliberately); share `model.py`/`provisioning.py`/
  `api_launcher.py`; vendor `third_party/colmap` once (only the
  variant that builds it keeps `scikit_build_core`). (L)
- [ ] 4.5 Absorb the 8× in-process `api_launcher.py`/`cli.py`
  boilerplate (~1,000 lines) into `sfmapi.runtime` (it already owns
  `create_app`). (M)
- [ ] 4.6 Normalize plugin metadata: shared README template, common
  test template (`test_public_boundary` etc. — vismatch lacks them),
  aligned versions. (M)
- [ ] 4.7 instantsfm: rename the hand-written `sksparse/cholmod.py`
  shim so it cannot shadow real `scikit-sparse`; centralize the
  `PYTHONPATH` injection into a shared helper. (S)

## Phase 5 — SDK consolidation

- [ ] 5.1 **Unblock deprecation**: migrate `sfmapi/bench/` (3 files)
  to `sfmapi_client_gen`; root the generated SDK's `SfmApiError` in
  itself (drop the `sfmapi_client.errors` import at
  `_ergonomics.py:31`); port the CLI (the one surface only the
  deprecated package has) onto the generated client. (M)
- [ ] 5.2 **Finish the TS migration Python already made**: generate
  the full TS client surface (today a 178-line stub vs the 1,400-line
  hand-rolled primary), flip `package.json` exports so generated is
  the root, deprecate the hand-rolled stack. (If instead hand-rolled
  is declared canonical: delete `_generated/client.ts` +
  `ergonomics.ts` and say so — either way stop shipping two.) (L)
- [ ] 5.3 **Wire-format single-sourcing**: move `_ergonomics.py` /
  `ergonomics.ts` out of `_generated/` trees (they are hand-written
  and only look codegen-owned); add **one golden-bytes fixture set**
  (server-owned) decoded by all three languages' SSE + binary
  parsers; point the C++ SDK's contract tests at the server fixtures
  instead of its private copies. (M)
- [ ] 5.4 **depth/normal formats decision**: they are in CLAUDE.md +
  all three SDK parsers but absent from the wire (module deleted,
  stale `.pyc` only). Either restore server-side emitters + routes,
  or delete the parsers/docs until a consumer exists (recommended). (M)
- [ ] 5.5 Add a version-coherence check: `openapi.json info.version`
  == server `__version__` == SDK package versions (extend the
  existing `.sdk_codegen.sha256` provenance check). Then actually
  ship **0.0.2** so the L12 deprecation milestone stops being
  fictional. (S–M)

## Phase 6 — Docs & decision-register reconciliation

- [ ] 6.1 **Register truth**: mark L9 unlocked/superseded (WebSocket
  shipped, spec'd in §8, wired at `main.py:343`); refresh P5's
  premise; add rows for D1–D4 outcomes. (S)
- [ ] 6.2 **CLAUDE.md refresh**: remove phantom `docs/phase_*.md` and
  `masksets.py`; fix untyped-route count (11, not 16 — or re-count
  from the guard test); fix the lint command; remove per-tier
  conftest claims; point plugin guidance at `sfmapi.runtime` (public)
  instead of `app.adapters.*`; document the depth/normal outcome from
  5.4. Same fixes in AGENTS.md. (S)
- [ ] 6.3 **Custom-verb normalization** (pre-1.0 window): rename the
  8 snake_case colon verbs (`:from_archive`, `:from_video`,
  `:import_kapture`, `:project_images`, `:render_cubemap`,
  `:render_equirectangular`, `:render_perspective`, `:to_cubemap`) to
  lowerCamel per L23; pick one style for pipelines (`/{recipe}` vs
  `:run`); document (or resolve) `:relocalize` vs `/localize` on the
  same resource. Regen SDKs after. (M)
- [ ] 6.4 Move working docs (`*_proposal.md`, `*_checklist.md`,
  audits incl. this one) out of the published Sphinx nav into an
  internal section or `docs/_internal/`; flip `nitpicky=True` once
  the stale `SfmBackend` xrefs are fixed. (M)
- [ ] 6.5 Add an MCP↔REST parity contract test (the 27-tool MCP
  surface is hand-mirrored and can drift silently). (M)
- [ ] 6.6 Note in SPEC/docs that the WebSocket surface is outside
  OpenAPI (SDK codegen cannot see it) and where its contract lives
  (§8 + `test_contract` coverage). (S)

## Phase 7 — Strategic (gated on Phase 0)

- [ ] 7.1 (D1) Implement kernel fencing: conformance-level tags in
  the spec + settings-driven `include_in_schema` for preview routers;
  default contract ≈ 100 ops; conformance tests assert the split. (M)
- [ ] 7.2 Retire one of operations-vs-processors: keep the Processor
  registry, delete the legacy Operation projection and
  `/v1/operations` (or keep it fenced as deprecated for one release). (M)
- [ ] 7.3 (D2) Execute the `sfmapi-cpp` decision (archive + pointer,
  or promotion + Python-surface freeze). (S to archive)
- [ ] 7.4 (D4) Fold `app/` under the `sfmapi` namespace; keep a
  temporary `app` shim module for one release if needed. (L)
- [ ] 7.5 (D3, optional) Evaluate monorepo for core + plugins after
  4.3/4.4 land; the repo count may already be tolerable at 11. (—)

---

## Suggested sequencing

Weeks are indicative for a single maintainer working part-time on this.

1. **Week 1:** Phase 1 (all) + Phase 2 quick wins (2.1, 2.2, 2.6–2.11).
2. **Week 2:** Phase 2 remainder (2.3–2.5) + Phase 0 decisions D1–D4.
3. **Weeks 3–4:** Phase 3 (3.1, 3.2, 3.5 first) and Phase 4.1/4.2.
4. **Weeks 5–6:** Phase 4.3/4.4 (repo merges) + Phase 5.1/5.5.
5. **After surface stabilizes:** Phase 5.2–5.4, Phase 6, Phase 7.

## Explicitly not doing

Per the existing decision register: no S3-cache unification (C1), no
settings submodels (C2), no C++ live-server test (C3), no checkpoint
unification (C4), no snapshot tar/copy protocols (C5/C6). This plan
adds no new abstractions beyond the descriptor-registry base (3.3)
and the plugin-service kit re-export (4.1), both of which replace
more code than they introduce.
