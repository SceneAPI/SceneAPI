# Jobs and progress

Every long-running operation is a **Job** that owns a DAG of **Tasks**.
Both live in the DB. Each Task is a single ARQ job at the executor
layer; ARQ doesn't know about the DAG, only the orchestrator does.

## Job lifecycle

```{mermaid}
stateDiagram-v2
    [*] --> pending
    pending --> running : worker leases task
    running --> succeeded
    running --> failed
    running --> cancelled : cancel request
    running --> cancelled_dirty : force cancel request
    failed --> pending : resume request
    cancelled --> pending : resume request
    succeeded --> [*]
```

A job stays in `pending` until at least one Task has been admitted by
a worker. Cancellation is **cooperative** between phases; only
`?force=true` SIGKILLs the subprocess (and triggers a worker restart
to flush the CUDA context).

## Cache lookup

When the orchestrator persists a Task, it computes
`cache_key = sha256(canonical_json({kind, inputs_hash, params_hash, rv_id}))`
and queries `task` for an existing row with the same `cache_key` and
`status='succeeded'`. If found, the new Task is *immediately* marked
`succeeded` and inherits the cached `outputs_ref_json` — no enqueue.

This is what makes `submit_features` followed by `submit_matches`
followed by re-submitting the same `submit_features` not re-extract:
the second call hits cache.

## Lease + heartbeat

```{eval-rst}
.. autofunction:: app.orchestrator.lease.try_acquire_lease
   :no-index:

.. autofunction:: app.orchestrator.lease.refresh_lease
   :no-index:
```

The pattern works on both SQLite and Postgres without dialect
branches. Workers refresh every `lease_ttl_seconds // 3`. If a worker
crashes, the next worker that scans pending tasks reclaims the lease
once `lease_expires_at < now()`.

## Fair-share scheduler

```{eval-rst}
.. automodule:: app.orchestrator.fair_share
   :members:
   :no-index:
```

Picks the next ready Task biased toward tenants with the smallest
running-task count, capped by `max_consecutive_per_tenant` to prevent
starvation when one tenant submits 100 jobs at once.

## ProgressEvent stream

Workers emit a versioned `ProgressEvent` for each phase boundary,
metric, snapshot seal, or warning/error. Events are written to two
sinks:

1. `events.jsonl` on disk — durable, used for SSE replay via
   `Last-Event-ID`.
2. `JobEvent` table — durable index for the API to count/serve.

The SSE endpoint streams from the DB (cursor on `event_id`) and
forwards new events live until the client disconnects.

## Choosing a progress endpoint

Use the three job reads for different purposes:

| Endpoint | Use when |
|---|---|
| `GET /v1/jobs/{id}` | You need canonical lifecycle state, task outputs, or final errors |
| `GET /v1/jobs/{id}/progress` | You need a small polling response for a CLI, web UI, or dashboard |
| `GET /v1/jobs/{id}/events` | You need every phase, metric, warning, log line, or snapshot event |

For polling clients, `GET /v1/jobs/{id}/progress` returns the current
job status, task counts, active task, latest event, and a best-effort
`progress` fraction. Use it for CLIs and dashboards that need a
snapshot rather than a live stream.

The progress fraction is computed from task state plus the latest
`phase_progress` event for each task. Terminal tasks count as `1.0`.
Running tasks use `current / total` when the backend reports both
values. Pending tasks and tasks with no measurable total count as
`0.0`. This makes the field stable for UI display without making it a
scheduler guarantee.

Example snapshot:

```json
{
  "job_id": "01J...",
  "recipe": "global",
  "status": "running",
  "progress": 0.42,
  "total_tasks": 4,
  "completed_tasks": 2,
  "task_counts": {"succeeded": 2, "running": 1, "pending": 1},
  "current_task_kind": "match",
  "current_phase": "matching",
  "latest_event_id": 182,
  "tasks": [
    {"kind": "extract", "status": "succeeded", "progress": 1.0},
    {"kind": "match", "status": "running", "progress": 0.68}
  ]
}
```

## Backend-reported progress

Backends may opt in to fine-grained percentages by accepting an
optional `progress` keyword on long-running methods. Workers pass this
reporter only when the method signature supports it, then persist the
reported `phase_progress` events. Report counts (`current` / `total`)
where possible; the API derives the `0.0` to `1.0` fraction from the
latest durable event.

Good totals are domain-specific:

| Stage | Useful total |
|---|---|
| Feature extraction | Number of input images |
| Exhaustive matching | Number of image pairs, `n * (n - 1) / 2` |
| Sequential matching | Number of selected neighboring pairs |
| Geometric verification | Number of candidate match pairs |
| Mapping | Registered images or backend-specific milestones when available |

Backend progress must remain best-effort. A reporter failure should be
logged or ignored by the backend, not fail the reconstruction job.

```{eval-rst}
.. automodule:: app.schemas.progress_event
   :members:
   :no-index:
```

## Resume

```{eval-rst}
.. autofunction:: app.orchestrator.resume.resume_job
   :no-index:
```

`POST /v1/jobs/{id}:resume` resets only `failed` /
`cancelled` / `cancelled_dirty` tasks back to `pending`, then
re-enqueues them (or runs inline in test mode). Mapping tasks
specifically pick up from the latest `MappingInput.pcmapin` checkpoint
in `jobs/{id}/checkpoints/`, so a multi-hour mapping run that crashed
at 80% picks up near 80% rather than restarting from scratch.

## Snapshot endpoints

| Endpoint | Returns |
|---|---|
| `GET /v1/reconstructions/{rid}/snapshots` | List of sealed `seq` numbers |
| `GET /v1/reconstructions/{rid}/snapshots/{seq}/cameras.json` | JSON |
| `GET /v1/reconstructions/{rid}/snapshots/{seq}/images.json` | JSON |
| `GET /v1/reconstructions/{rid}/snapshots/{seq}/points.bin` | `application/x-sfm-points-v1` |
| `GET /v1/reconstructions/{rid}/snapshots/{seq}/points_preview.bin` | decimated |

The API path-traversal-checks `name`, then serves the file directly
with `FileResponse`. No DB read, no backend import, no race risk.
