# Gap-Closure Checklist

Consolidated punch list from the multi-round provider-routing + plugin-completeness
audit. Each item is classified, points at the file(s) to touch, and notes whether
it's a straight fix or needs a decision. Tiers are ordered by impact:

- **Tier 1** — correctness gaps: something is broken or actively misleading *now*.
- **Tier 2** — completion gaps: declared/scaffolded but not finished end-to-end.
- **Tier 3** — hygiene: dead code, doc drift, cleanup.
- **Tier 4** — tests + architecture: coverage and one open design question.

Nothing here is a regression — it is the residue of incremental work. Audited
surfaces: API/schemas, workers/orchestrator/storage, sfm_hub/CLI, the SDK repo,
and SFMAPI-SPEC vs implementation.

## Status — all items closed

Every box below is ticked. Three items resolved by judgment call rather than
literal implementation:

- **`compute.in_memory` non-path Protocol** — closed as a design proposal
  (`docs/_internal/in_memory_reconstruction_proposal.md`, decisions.md **P6**), not
  code: fork-per-task + one-Task-one-ARQ-job bound any in-memory handle to
  within-Task scope, so the Protocol is premature. Per the original "design
  only" framing.
- **Real-engine CI lane** — closed at the "at minimum" bar: env-skip matrix
  documented (`docs/guides/real_engine_testing.md`) + a `workflow_dispatch`-only
  `real-engine` skeleton job in `ci.yml`. The COLMAP-build step is left to be
  hardened per-runner.
- **Backend-action provider resolution** (part of the utility-stage item) — left
  as explicit-provider-only on purpose: a backend action's `action_id` namespace
  (`colmap.*`) already identifies its backend, so routing-profile resolution
  keyed on a portable "stage" does not apply. The portable utility *stages*
  (`project_images` / `render_cubemap` / `merge_recons` / `to_cubemap` /
  `georegister` / `localize` / `vlad_index` / `convert_artifact`) all now call
  `apply_provider_resolution`.

---

## Tier 1 — Correctness

- [x] **Wire cooperative cancellation.** `POST /v1/jobs/{id}:cancel` flips
      `Job.cancel_requested` / `cancel_force` but nothing consumes them —
      `dispatcher.execute_task` never checks the flags and never sets a task to
      `cancelled` / `cancelled_dirty`, so `_maybe_finalize_job`'s `cancelled`
      branch is dead and a cancelled job runs to completion.
      → `app/workers/dispatcher.py`, `app/services/job_service.py:150`,
      `app/orchestrator/cancel.py` (CLAUDE.md layout expects this file).
- [x] **Add the janitor.** `settings.janitor_interval_seconds` is defined but
      there is no `janitor.py`, no lease-reclaim sweep, and no scheduled hook in
      `app/main.py` / the ARQ `WorkerSettings`. A task whose worker dies stays
      `running` forever. The `uploads` "reaped by the janitor" doc claim is also
      unbacked. → new `app/orchestrator/janitor.py` + wire into worker bootstrap;
      `app/schemas/api/uploads.py:19`.
- [x] **Echo the resolved `provider`.** Core SfM stages run
      `apply_provider_resolution()` and mutate the spec with a resolved provider,
      but `sfm_stages.py` builds `JobAcceptedResponse` with no `provider`, and
      `provider` is absent from `JobDetail` / `TaskOut` entirely — so when the
      server picks a provider via routing profile/priority the client can never
      learn which one. → `app/api/v1/sfm_stages.py:62`,
      `app/schemas/api/jobs.py` (add `provider` to `JobDetail`/`TaskOut`).
- [x] **Run provider resolution on utility stages.** `apply_provider_resolution`
      (routing profiles, priority, `ProviderAmbiguityError`) is wired into the 5
      core stages only. `submit_render_cubemap` / `submit_project_images` /
      `submit_merge_recons` / `submit_to_cubemap` / `submit_georegister` /
      `submit_localize` / `submit_vlad_index` / artifact-convert / backend-action
      honor an *explicit* provider but never resolve one — contradicting
      SFMAPI-SPEC §6.6 ("utility stages accept the same selector ... MUST raise
      `ProviderAmbiguityError`"). → `app/services/sfm_stage_service.py`,
      `app/services/{artifact_conversion,backend_action}_service.py`.
- [x] **Add `extra="forbid"` to inline request models.** `LocalizationRequest`,
      `MergeRequest`, `VideoFramesRequest`, `KaptureImportRequest`,
      `PipelineRequest` use `populate_by_name` with no `extra="forbid"` — a
      typo'd `provder` is silently dropped and the job runs on the wrong backend.
      → `app/api/v1/{localize,reconstructions,projects,pipelines}.py`.
- [x] **Make `doctor` actually probe.** `sfm_hub/doctor.py::doctor_manifest`
      hardcodes the `"manifest"` check to `status="pass"` and only runs a real
      probe for `external_tool` plugins — `doctor` can never return `fail` for a
      uv or docker plugin and never verifies the package imports / entry point
      loads / docker image exists. → `sfm_hub/doctor.py:104-153`.
- [x] **Fix the error `type` URI in the spec.** SFMAPI-SPEC.md:351 uses
      `https://sfmapi/errors/capability_unavailable`; every other example, plus
      `app/core/errors.py:34` and all fixtures, use
      `https://sfmapi.github.io/errors/<slug>`. → SFMAPI-SPEC.md:351.
- [x] **Re-run `scripts/regen_sdk.py` and commit the SDK repo.** The generated
      Python + TS SDKs are stale: the current OpenAPI carries `provider` on ~34
      surfaces, the SDK's working tree has ~25. Missing `provider`:
      `LocalizationRequest`, `MergeRequest`, `ProjectionJobRequest` (+ the 3
      projection request subclasses), and the `similarity:build` /
      `:georegister` / `:to_cubemap` query params. The SDK repo also has an
      *uncommitted partial regen* run against a stale checkout — redo cleanly.
      → `scripts/regen_sdk.py`, then commit `../sfmapi-sdk`.

## Tier 2 — Completion

- [x] **Portable job-submission routes for the 6 Tier-3 capabilities.** The L36
      follow-up. `image.undistort`, `georegister.gps`, `index.vocab_tree`,
      `rigs.configure`, `geometry.two_view`, `ba.rig` have Protocol methods +
      backend implementations and are reachable via the backend-action catalog,
      but no portable `POST /v1/...` route → service → worker → spec. Per
      capability: spec schema (with `provider`), API route, `sfm_stage_service`
      builder (calling `apply_provider_resolution`), worker task, e2e test.
      Do `image.undistort` first as the template. → `app/schemas/`,
      `app/api/v1/`, `app/services/sfm_stage_service.py`, `app/workers/tasks/`.
- [x] **Decide the fate of the 6 orphaned task handlers.** `ba`, `triangulate`,
      `pgo`, `export`, `relocalize`, `render_cubemap` are registered via
      `@task_handler` but no service / DAG builder ever creates those task kinds
      (`render_cubemap` because `submit_render_cubemap` emits a `project_images`
      task instead). Either give them submit paths (overlaps the Tier-2 routes
      item) or delete the dead handlers. → `app/workers/tasks/{ba,triangulate,pgo,export,relocalize,render_cubemap}.py`.
- [x] **Consume `pose_priors` in global/hierarchical mapping** — or scope the
      `pose_priors.mapping` capability to incremental-only in its docstring.
      Round-2 wired priors into `incremental` mapping via `pose_prior_mapper`;
      global/hierarchical silently ignore them. → COLMAP-family plugin
      `run_mapping`, `app/core/capabilities.py`.
- [x] **Validate bundled-manifest capability strings against `ALL_KNOWN`.**
      `sfm_hub/models.py` `_capabilities_are_unique` only dedups/sorts; a typo
      like `features.extract.sif` in a `registry/backends/*/manifest.json` passes
      CI and makes the provider silently unroutable. → `sfm_hub/models.py:134`
      (add a `field_validator`) or a `sfm_hub/registry/__init__.py` load assertion.
- [x] **Validate manifest field formats.** `provider_id`, `entry_points`
      (`module:attr` shape), `github_url`, `UvRuntime.url` are bare `str` with no
      regex — malformed values are invisible until install time.
      → `sfm_hub/models.py:18,58,107,108`.
- [x] **Validate routing-profile providers on write.** `upsert_profile` (and the
      CLI `profiles create` / admin `POST /routing/profiles`) accept arbitrary
      `{stage: [provider_id]}` with no existence check — a typo'd provider
      silently no-ops at routing time. `set_default_profile` etc. *do* validate
      the profile name, so the asymmetry is an oversight. → `sfm_hub/state.py:120`.
- [x] **C++ SDK POD structs for the new request bodies.** `cpp/include/sfmapi/specs.hpp`
      models only the SfM stage specs; `LocalizationRequest`, `MergeRequest`,
      `ProjectionJobRequest`, `JobAcceptedResponse` have no POD struct — C++
      `Submit*` methods take raw JSON strings. Decide whether to close this
      pre-existing 3-language parity gap. → `../sfmapi-sdk/cpp/include/sfmapi/`.
- [x] **hloc retrieval-method selection.** hloc ships NetVLAD / DIR / OpenIBL /
      MegaLoc but sfmapi exposes one flat `pairs.retrieval`. Add a `retrieval_conf`
      enum to hloc's `hloc.pairs.retrieval` config schema (config-key, not vocab
      sprawl). → `../sfmapi_hloc/src/sfmapi_hloc/backend.py`.
- [x] **COLMAP 3.13 `global_mapper` rename.** `COLMAP_BACKEND_CONFIGS` targets the
      COLMAP 4.1 command surface; 3.13 renamed `global_mapper`, so real-executable
      contract checks fail. Make the command map version-adaptive (probe
      `colmap help`) or document the pinned-version assumption.
      → COLMAP-family plugins' `cli.py`.

## Tier 3 — Hygiene / docs / dead code

- [x] **CLAUDE.md points-binary header size.** CLAUDE.md:84 and :116 say "32 B
      header"; the truth is 44 (`app/schemas/points_binary.py:29`, and
      SFMAPI-SPEC §7.1 is correct). → CLAUDE.md.
- [x] **Add implemented routes to the spec tables.** Missing from §6.x / §7:
      `GET /v1/artifacts/{kinds,formats,/{id},/{id}/content}`, `GET /v1/jobs`,
      `GET /v1/jobs/{id}/{progress,artifacts}`,
      `GET /v1/reconstructions/{id}/artifacts`, the snapshot submodel file route,
      `GET /v1/camera-models`, `GET /v1/spec`. → SFMAPI-SPEC.md §6.1/§6.7/§6.9/§7.
- [x] **Resolve the §10 vs Appendix D dense/mesh contradiction.** §10 lists
      "dense reconstruction / mesh extraction" as MAY-implement; Appendix D says
      explicitly out of scope. Pick one and scrub the stale
      `JobAcceptedResponse` docstring ("the localize / dense / mesh / cubemap
      stages"). → SFMAPI-SPEC.md:1705 + 1811, `app/schemas/api/jobs.py:155`.
- [x] **Remove C++ SDK vaporware.** `cpp/include/sfmapi/client.hpp` has
      `SubmitDense`, `SubmitMesh`, `ReadDenseIndex/Fused`, `ReadDepthMap`,
      `ReadNormalMap`, and a dense-MVS doc example — no dense or mesh routes
      exist and `dense.*` was purged from the vocabulary.
      → `../sfmapi-sdk/cpp/include/sfmapi/client.hpp`.
- [x] **`install_plugin` over an existing install silently overwrites.**
      `record_install` / `record_manual_install` clobber the prior record with no
      warning / no `--force`. Decide: warn, require force, or idempotent-by-ref.
      → `sfm_hub/state.py:76,99`, `app/services/plugin_service.py`.
- [x] **Decide on the unused manifest fields.** `conformance`, `compatibility`,
      `licenses`, `upstream_projects` are parsed but no runtime code reads them.
      Either wire `compatibility` into `install_plugin` as a pre-flight gate +
      surface `conformance`/`licenses` in `doctor`/`get_plugin`, or document them
      as informational-only. → `sfm_hub/models.py`, `app/services/plugin_service.py`.
- [x] **Decide on dead orchestrator code.** `fair_share.pick_next_task` /
      `FairShareState` and `JobDag.topo_order` are referenced only by tests — no
      supervisor calls fair-share, and `submit_job_dag` enqueues in list order
      without topo-sorting. Wire them in or delete them.
      → `app/orchestrator/{fair_share,dag}.py`.
- [x] **`ArtifactImportRequest` forward-ref cleanup.** `app/schemas/api/artifacts.py`
      references `ArtifactFileRef` before its definition (works via
      `from __future__ import annotations`, but reorder for clarity).

## Tier 4 — Tests + architecture

- [x] **e2e tests for the new portable stages** (depends on Tier-2 routes).
- [x] **Real-engine CI lane.** Most plugin tests mock the engine or skip when no
      runnable COLMAP / pycolmap / sample data is present. Add a CI job with a
      real COLMAP build + the South Building sample, or at minimum document the
      env-skip matrix.
- [x] **`localize.from_memory` real test for pycolmap** — currently only the
      wiring is unit-tested; `estimate_and_refine_absolute_pose` runs only under
      the env-gated `needs_pycolmap` tests.
- [x] **C++ contract test should surface `provider`.** `test_contract.cpp`
      decodes `job_accepted_features.json` (now carrying `provider`) but
      `ParseJobSubmitResponse` drops it.
- [x] **Contract fixtures for the new provider-carrying surfaces** — localize /
      merge / projection / similarity responses have no recorded fixtures.
- [x] **`matchers.mast3r` — implement or remove.** It is in `OPTIONAL_CAPABILITIES`
      but no plugin backs it. Either wire it in a plugin or drop it from the
      vocabulary until a backend implements it.
- [x] **MCP discovery for the new capabilities** — the Tier-3 capabilities aren't
      surfaced through MCP tools yet; extend once the Tier-2 routes exist.
- [x] **`compute.in_memory` — non-path reconstruction-handle Protocol** (design).
      The capability advertises a no-materialization execution *mode*, but the
      backend Protocol is filesystem-path-oriented end-to-end (`database_path:
      Path`, `model_path: Path`). The C++ in-memory backend could hold in-process
      reconstruction handles / do streaming incremental mapping — currently
      inexpressible. Warrants a proposal doc + a `decisions.md` "Proposed" entry,
      not a quick fix.

---

## Suggested sequencing

1. **Tier 1** first — these are correctness bugs / misleading behavior. The
   cancellation + janitor items are the most consequential (a cancelled or
   orphaned job currently has no terminal path). The provider-echo + utility-stage
   resolution items close a spec violation introduced by the recent rounds.
2. **Tier 2** — the portable-route work is the headline; the orphaned-handler
   decision should be made *with* it (implementing the routes may revive some
   handlers, deleting them is the alternative). Manifest validation is independent
   and quick.
3. **Tier 3** — mechanical; batch into one cleanup pass per repo.
4. **Tier 4** — tests follow Tier 1/2; the `compute.in_memory` Protocol question
   is the one genuine open architectural decision and should become a proposal
   doc before any code.
