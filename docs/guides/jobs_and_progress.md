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
