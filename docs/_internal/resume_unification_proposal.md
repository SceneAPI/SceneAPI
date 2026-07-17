# Resume Primitive Unification — Design Proposal

**Status**: Proposal. Not implemented. Reviewed but not committed.

**Owner**: TBD.

**Companion**: `docs/guides/architecture.md` (current state),
`docs/phases/phase_5_resume_tenancy_s3_obs.md` (Phase 5 plan).

---

## The problem

sfmapi currently has **three** distinct primitives that all answer
some variant of "where can we come back to?":

| Primitive | Owner | Purpose | Storage | Lifetime |
|---|---|---|---|---|
| `pycolmap.MappingInput.save/load` | `app/storage/mapping_input.py` | Resume an *in-progress* incremental mapping mid-task | Binary blob `jobs/{job_id}/checkpoints/{seq}.pcmapin` | Per-job |
| Sealed snapshot | `app/storage/snapshots.py` | Stable read-only view of a reconstruction at a point in time | Atomic dir rename `reconstructions/{rid}/snapshots/{seq:08d}/` | Append-only forever |
| `Task.cache_key` | `app/orchestrator/dag.py`, `app/services/job_service.py::lookup_cached_task` | Skip re-running an identical task whose result is still on disk | Row in DB (cache_key column) | Until upstream invalidates |

Each one has a real, distinct job. But they overlap awkwardly:
- Cache hits and `MappingInput.load` both let you *skip work*.
- Sealed snapshots and `MappingInput` both *materialize state at
  a moment in time*.
- Resume (`POST /v1/jobs/{id}:resume`) uses **all three**:
  preserves `cache_key`s, replays from latest `MappingInput`, and
  reads sealed snapshots to inspect what's already done.

The result: mental model is fuzzy; the resume code path
(`app/orchestrator/resume.py`) has to be aware of all three; cross-cutting
concerns (cleanup, GC, observability) are split three ways.

## What each one actually does — in one sentence

1. **`MappingInput`** — *"the worker's process state at sub-task
   granularity, so it can pick up mid-incremental without re-running
   the prior phases."* Write-side requires pycolmap; binary; not
   inspectable from the API.

2. **Sealed snapshot** — *"an append-only, atomically-published view
   of a reconstruction's outputs at the end of a stage, addressed by
   monotonically-increasing `seq`."* Read-side is the entire API
   contract for "what does the reconstruction look like right now?".

3. **`Task.cache_key`** — *"a content-address of inputs+params so a
   re-submission of the same logical work returns the prior task's
   `outputs_ref_json` instead of re-running."* Lives in the
   orchestrator; never crosses the API.

The overlap is real but small: **`MappingInput`** and **sealed
snapshot** both describe state at a moment in time, but at different
granularities (sub-task vs end-of-stage) and for different audiences
(worker vs API consumer). **`cache_key`** doesn't *describe* state at
all — it indexes whether a task ran successfully before.

## Conclusion: don't unify

After mapping out the three primitives, the conclusion is they
**should not be unified into a single concept**. They solve genuinely
different problems:

- A unified "Checkpoint" type that subsumed all three would have to
  carry: opaque worker bytes (MappingInput), a sealed dir + manifest
  (snapshot), AND a cache key. The union type is wider than the
  intersection of usefulness, and forcing every consumer to figure
  out which fields apply at any given moment is worse than three
  named things.

- The seemingly-shared property "comes back to a place" hides the
  real semantic difference. **Snapshots are publishable**;
  `MappingInput` is not (it carries internal worker state). **Cache
  keys are content-addressed**; sealed snapshots are
  sequence-addressed.

What the resume code *does* need is a shared lookup helper that, given
a `(job_id, recon_id)`, returns the latest of each of the three
primitives in one call — so `app/orchestrator/resume.py` doesn't
hand-roll three lookups. That's a 30-line refactor, not an
architectural change.

## What to actually do — three concrete actions

### 1. Rename for clarity. (Cheap, immediate.)

The current names create the false impression of conceptual overlap.
Rename to make the distinct purposes obvious in code:

| Current | Proposed |
|---|---|
| `MappingInput` checkpoint | **`mapping_state`** — "the worker's mapping state at a point" |
| sealed snapshot | **`recon_snapshot`** — "a published reconstruction view" |
| `Task.cache_key` | (no rename — already accurate) |

Touch points: `app/storage/mapping_input.py` → `mapping_state.py`,
the few `MappingInput` references in `app/adapters/colmap_backend.py`
and `app/workers/tasks/map.py`. Snapshot rename is bigger because of
SDKs. **Defer the snapshot rename until the next major SDK version
bump.** Do the mapping_state rename now.

### 2. Add a shared `resume_inventory(job_id, recon_id)` helper.

Returns a typed `ResumeInventory`:
```python
@dataclass
class ResumeInventory:
    latest_mapping_state: CheckpointRef | None  # MappingInput
    latest_snapshot_seq: int | None             # sealed snapshot
    cached_task_count: int                      # cache_key hits
    succeeded_task_count: int
    pending_task_count: int
```

Lives in `app/services/resume_service.py` (new file).
`app/orchestrator/resume.py` calls it once instead of querying each
of the three primitives separately. Easier to instrument, easier to
test, lifts the "what's resumable?" question out of the resume
machinery.

### 3. Document the three-way split as canonical.

Add a top-level "Resumability primitives" section to
`docs/guides/architecture.md` enumerating the three primitives, their
purposes, their lifetimes, and their non-overlap. Cross-link from
`app/orchestrator/resume.py`'s docstring.

The existing pattern of "find out by reading three different files"
is worse than the technical complexity itself.

## What this proposal explicitly does NOT do

- **Does not** introduce a new `Checkpoint` abstraction.
- **Does not** change wire formats or SDK types.
- **Does not** change the resume API surface.
- **Does not** depend on the Postgres-only commit, the S3 sealed
  snapshot work, or any pycolmap version.

## Estimated cost

- Mapping_state rename: 2-3 files, ~20 LOC churn, no test changes
  beyond import-path fixes. **~30 min**.
- `resume_service.py` + helper: 1 new file (~80 LOC) + 1 small
  refactor in `resume.py` (~20 LOC removed). **~1 hour**.
- Architecture doc section: 1 file edit. **~15 min**.

**Total: ~2 hours of mechanical work, no design risk.**

## Decision — recommend approving

Most of the perceived complexity in the resume layer is naming
and code-locality, not architecture. A unified primitive would have
been over-engineering; the three things really are different. The
recommended actions all reduce confusion without changing semantics
or breaking consumers. Do them.
