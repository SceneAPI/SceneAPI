# In-Memory Reconstruction Handle — Design Proposal

**Status**: Proposal. Not implemented. Design only — no code this pass.

**Owner**: TBD.

**Companion**: `app/adapters/backend.py` (the stage Protocols),
`app/core/capabilities.py` (`compute.in_memory`), `CLAUDE.md`
§"Locked Constraints" / §"Locked Tech Decisions".

---

## The problem

Every portable stage Protocol in `app/adapters/backend.py` is
**path-addressed**. `RefinementBackend.bundle_adjustment`,
`MappingBackend.run_mapping`, `ExportBackend.export`,
`TransformBackend.apply_sim3`, etc. all take `model_path: Path` /
`database_path: Path` / `output_path: Path`. A stage reads a model
off disk, does its work, and writes a model back to disk; the next
stage reads that.

The `compute.in_memory` capability flag (added in the round-2 vocab
extension, L36) was reserved for a backend that "can run portable
stages without materializing intermediate artifacts (no on-disk
database / sparse model) — e.g. an in-process COLMAP bridge." Today
that flag is **purely advisory** — there is no Protocol surface a
backend can implement to actually *exercise* in-memory execution. A
backend that advertises `compute.in_memory` still gets handed `Path`
arguments and still has to round-trip through the filesystem between
every stage.

The open question: should sfmapi grow a **non-path reconstruction
handle** — an opaque object a backend can pass between stages instead
of a filesystem path — so an in-memory backend can chain
`map → ba → triangulate → pgo` without writing the sparse model to
disk four times?

## Why this is not as simple as "add a handle type"

Three locked constraints collide with a persistent in-memory handle:

1. **One Task = one ARQ job (L5).** The DAG's edges are between
   *Tasks*, and each Task is dispatched independently. A handle
   produced by the `map` Task does not exist in the address space of
   the `ba` Task — they may run minutes apart, on different workers.

2. **Fork-per-task subprocess (the supervisor model, `CLAUDE.md`
   §"Locked Tech Decisions" → workers).** Even on one worker, each
   Task runs in its own subprocess. A Python object holding a
   reconstruction cannot survive `os.fork` + `exec` boundaries. The
   only thing that crosses a Task boundary is **bytes on disk** (or
   in a blob store).

3. **API reads sealed snapshots only (L4).** The API never sees a
   live reconstruction object regardless. Even a fully in-memory
   backend must *seal a snapshot* for the reconstruction to be
   readable over the wire. So the in-memory win is never
   API-visible; it is strictly a *worker-side, cross-stage* I/O
   saving.

The consequence: a reconstruction handle that is genuinely "not a
path" can only live **within a single Task's subprocess**. Across
Tasks, the handle *must* serialize — and the moment it serializes, it
is just `MappingInput` (`PCMAPIN\0` v1, the existing canonical
cross-stage + resume primitive) or a sealed snapshot again.

## Options

### Option A — Do nothing structural; document the real mechanism

Keep every Protocol path-addressed. State plainly that
`compute.in_memory` is realized by **fusing multiple stages into a
single Task** — the same way `/pipelines/{recipe}` already strings
`extract → match → verify → map` into one DAG, a recipe could string
`map → ba → triangulate` into *one Task* whose handler keeps the
reconstruction object alive in-process for the whole chain. The
backend never sees a `Path` for the intermediate models because the
intermediates never become Tasks. `compute.in_memory` stays advisory:
it tells the scheduler "this backend is cheap to fuse — prefer a
fused-Task plan for it."

- **Cost**: documentation + (later, separately) a fused-recipe Task
  kind. No Protocol change.
- **Doesn't do**: cross-*Task* in-memory chaining (impossible under
  L5 + fork-per-task anyway).

### Option B — Add an opaque `ReconstructionHandle` Protocol surface

Introduce a `ReconstructionHandle` (a backend-defined opaque object)
and `*_handle` variants of the stage methods that accept/return it
instead of `Path`. The worker would call the handle variant when the
backend advertises `compute.in_memory` *and* the next stage runs in
the same Task.

- **Cost**: doubles the stage Protocol surface (a `_handle` variant
  per method); the worker needs a "same-Task?" branch; every backend
  that opts in maintains two code paths.
- **Doesn't do**: still can't cross Task boundaries — so it only ever
  fires inside a fused Task, which Option A already enables *without*
  a Protocol split. The handle type is dead weight outside the fused
  case.

### Option C — Session-scoped long-lived backend process

A backend process that outlives individual Tasks, holding
reconstruction objects in memory keyed by id, with stages as RPC
calls into it.

- **Cost**: breaks L1 (one GPU per instance, per-GPU concurrency 1 —
  a session process holding GPU memory across Tasks fights the
  single-slot model) and L5 (Task ≠ ARQ job anymore). Effectively a
  different execution architecture.
- **Doesn't do**: anything that justifies unwinding two locked
  constraints.

## Recommendation — Option A

The in-memory win is real but **bounded by architecture to
within-Task scope**. Options B and C both try to extend it across
Tasks and pay for it by either doubling the Protocol surface (B) or
unwinding locked constraints (C) — for a benefit that Option A
already captures by fusing stages into one Task.

Concretely, recommend:

1. **Document** `compute.in_memory` as a *scheduler hint*, not a
   Protocol contract: it means "this backend is cheap to run as a
   fused multi-stage Task; prefer a fused plan." Update the capability
   comment in `app/core/capabilities.py` and `SFMAPI-SPEC.md` §3.11.
2. **Defer** any `ReconstructionHandle` Protocol until a fused-recipe
   Task kind exists and a concrete backend demonstrates the
   within-Task chaining is the bottleneck. The fused-Task kind is the
   real prerequisite; the handle type is a possible *internal*
   optimization of that handler, not a wire/Protocol concern.
3. **Keep** `MappingInput` as the one and only cross-Task
   reconstruction-state serialization format. Do not grow a second.

## What this proposal explicitly does NOT do

- **Does not** add a `ReconstructionHandle` type or `*_handle`
  Protocol methods.
- **Does not** change any existing path-addressed stage Protocol.
- **Does not** add a fused-recipe Task kind (that is a separate,
  larger proposal — this one only argues it is the *correct
  prerequisite* and that the handle Protocol is premature without it).
- **Does not** change the meaning of sealed snapshots or the API's
  read contract (L4 is untouched).

## Estimated cost

- Documentation-only for the recommended path: capability-comment
  edit + one `SFMAPI-SPEC.md` §3.11 paragraph. **~30 min.**
- The deferred fused-recipe Task kind (out of scope here) would be a
  multi-day effort touching the DAG builder, a new task handler, and
  the recipe routes — to be proposed separately if/when a backend
  needs it.

## Decision — recommend approving the documentation clarification, deferring the Protocol

`compute.in_memory` should remain advisory and be documented honestly
as within-Task-scoped. A non-path reconstruction-handle Protocol is
premature: it cannot cross Task boundaries under the locked execution
model, and within a Task it is an implementation detail of a
not-yet-existing fused-recipe handler — not a wire contract. Approve
the doc clarification; revisit the handle only after a fused-Task kind
exists and profiles as I/O-bound.
