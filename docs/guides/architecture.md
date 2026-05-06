# Architecture

`sfmapi` separates a thin always-on **web tier** from one or more
**GPU workers**. The web tier never imports `pycolmap`, `torch`, or any
heavy ML dep — those live in worker subprocesses behind the
`app/adapters/` boundary. State lives in three durable stores: a SQL DB
(SQLite or Postgres), a content-addressed blob store on disk, and a
sealed-snapshot directory tree per reconstruction.

```{mermaid}
flowchart LR
    subgraph Client
        SDK[sfmapi-client]
        UI[CLI / curl / browser]
    end

    subgraph Web tier
        FastAPI[FastAPI app]
    end

    subgraph Persistence
        DB[(SQLite or Postgres)]
        Blobs[(blobs/&lt;sha&gt;)]
        WS[(workspaces/&lt;tenant&gt;/...)]
    end

    subgraph Queue
        Redis[(Redis)]
    end

    subgraph "Worker host(s)"
        Sup[Supervisor (per GPU)]
        Sub1[Subprocess]
        Sub2[Subprocess]
        Sup --> Sub1
        Sup --> Sub2
        Sub1 --> Pycolmap[pycolmap / SAM]
    end

    SDK --> FastAPI
    UI --> FastAPI
    FastAPI -->|writes| DB
    FastAPI -->|writes| Blobs
    FastAPI -->|enqueue| Redis
    FastAPI -->|reads sealed| WS

    Sup -->|polls + leases| DB
    Sup -->|consumes| Redis
    Sub1 -->|reads bytes| Blobs
    Sub1 -->|writes db.db, sparse/| WS
    Sub1 -->|writes snapshots/{seq}/| WS
    Sub1 -->|writes events.jsonl| WS
    FastAPI -->|tails events.jsonl| WS
```

## Boundaries

| Layer | Imports | Notes |
|---|---|---|
| `app/api/` | only `app.core`, `app.db`, `app.schemas`, `app.services`, `app.orchestrator` | web process. Must start in <2s. |
| `app/services/` | `app.db`, `app.storage`, `app.orchestrator` | tenant-scoped CRUD, transactions, DAG construction |
| `app/orchestrator/` | `app.db`, `app.workers.runner` | DAG, lease, scheduler, recipes, resume |
| `app/workers/` | `app.adapters` only | runs in subprocess; per-task lease + heartbeat |
| `app/adapters/` | `pycolmap`, `torch`, `cv2`, ... | **only** layer that touches heavy deps |

A test (`tests/unit/test_app_starts.py`) enforces that importing
`app.main` does not pull in `pycolmap`, `torch`, `cv2`, or
`segment_anything`. CI fails if any of those leak.

## Why a custom DAG instead of using ARQ chains

ARQ is a great task runner, but its `chain` semantics don't model the
properties we need:

- **Per-task cache lookup**: each Task carries
  `(inputs_hash, params_hash, runtime_version_id) → cache_key`; an
  identical Task that has already produced output short-circuits to
  the cached `outputs_ref` without enqueuing.
- **Cancellation atomicity**: a single DB flag + cooperative check
  inside the worker between phases. Hard-kill = subprocess SIGKILL +
  worker restart, marked `cancelled_dirty`.
- **Resumability**: failed tasks reset to `pending` while succeeded
  tasks stay; the cache key is the contract.

ARQ remains the *executor* — one ARQ job = one Task. The DAG itself
lives in `task` rows and `depends_on_json`.

## Why sealed snapshots

pycolmap mutates `database.db` and `sparse/` in place. Reading those
while the worker is writing them produces torn protobuf and sometimes
SIGSEGV. The worker periodically copies `sparse/` to
`snapshots/.tmp_{seq}/` then `os.replace`s the directory atomically;
the API only ever serves data from sealed `snapshots/{seq}/` dirs.

```{mermaid}
sequenceDiagram
    participant Worker
    participant Disk
    participant API
    participant Client

    Worker->>Disk: write sparse/0/... (in place)
    loop every 50 image registrations
        Worker->>Disk: copy sparse/ -> snapshots/.tmp_42/
        Worker->>Disk: write snapshots/.tmp_42/.complete
        Worker->>Disk: os.replace(.tmp_42, 00000042)
        Worker->>API: emit ProgressEvent(snapshot_available, seq=42)
    end
    Client->>API: GET /reconstructions/R/snapshots/42/points.bin
    API->>Disk: serve immutable file
    API-->>Client: bytes
```

## Why the runtime version vector

The SfM backend (typically a pycolmap fork) ships new builds
frequently. A reconstruction cached against backend SHA `abc` is
not equivalent to one cached against `def`, even if the spec is
identical. The cache key includes a `runtime_version_id` derived
from `{colmap_sha, baxx_sha, cudss_ver, cuda_arch, sam_model_sha,
seed}`, so a worker upgrade automatically invalidates cached
output. The exact field names are backend-specific; the principle
is generic.

## Storage layout

```text
workspaces/{tenant_id}/
  blobs/{aa}/{sha256}                 # uploaded bytes (refcounted)
  _cache/s3/{bucket}/{key-hash}       # global S3 LRU
  projects/{pid}/datasets/{did}/
      manifest.json
      masks/{maskset_id}/...
  projects/{pid}/reconstructions/{rid}/
      database.db                     # touched ONLY via pycolmap.Database
      sparse/{idx}/                   # live, worker-only
      snapshots/{seq}/{idx}/          # sealed, atomic-rename; API reads these
      latest                          # text file: latest sealed seq
      manifest.json
  projects/{pid}/jobs/{jid}/
      log.jsonl                       # per-job structured log
      events.jsonl                    # ProgressEvent stream (SSE replay)
      checkpoints/{seq}.pcmapin       # MappingInput resume points
```

## Further reading

- [Storage abstraction](storage.md): blob store, ImageSource (upload/local/S3), snapshot writer.
- [Jobs and progress](jobs_and_progress.md): DAG, lease, cancellation, SSE.
- [Multi-tenancy](multitenancy.md): the day-1 scaffold, auth, quotas, fair-share.
- [Server modules](../server/orchestrator.md): autodoc reference.
