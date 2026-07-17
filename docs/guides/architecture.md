# Architecture

sfmapi separates a thin always-on **web tier** from one or more
**workers** that drive a registered SfM backend. The web tier never
imports an engine library (pycolmap, torch, segment_anything, ...) —
those live in backend packages outside this repo, accessed only
through the backend protocols behind the `sceneapi/server/adapters/` boundary.
Backends may implement a smaller protocol layer when they only expose
native actions or a subset of portable stages.

State lives in three durable stores: a SQL DB (SQLite or Postgres),
a content-addressed blob store, and a sealed-snapshot directory
tree per reconstruction.

```{mermaid}
flowchart LR
    subgraph Client["Client"]
        SDK["SDKs and clients"]
        UI["CLI, curl, browser"]
    end

    subgraph Web["Web tier"]
        API["FastAPI app"]
        Inline["Inline queue"]
        API --- Inline
    end

    subgraph WorkerPkg["Backend package"]
        Backend["Backend / SfmBackend implementation"]
        Engine["SfM engine"]
        Backend --> Engine
    end

    subgraph Persistence["Persistence"]
        DB[(SQL database)]
        Blobs[(Blob store)]
        WS[(Workspace files)]
    end

    subgraph Multi["Optional multi-instance mode"]
        Redis[(Redis)]
        Sup["Supervisor and workers"]
    end

    TaskRunner["Task runner"]

    SDK --> API
    UI --> API
    API -->|writes| DB
    API -->|writes| Blobs
    API -->|reads snapshots| WS

    Inline -.->|standalone| TaskRunner
    Sup -.->|polls and leases| DB
    Sup -.->|consumes| Redis
    Sup -.-> TaskRunner
    TaskRunner --> Backend
    Backend -->|reads bytes| Blobs
    Backend -->|returns artifacts| TaskRunner
    TaskRunner -->|seals snapshots| WS
    TaskRunner -->|writes events| WS
    API -->|tails events| WS
```

## Boundaries

| Layer | Imports | Notes |
|---|---|---|
| `sceneapi/server/api/` | only `sceneapi.server.{core,db,schemas,services,orchestrator}` | web process. Must start in <2s. |
| `sceneapi/server/services/` | `sceneapi.server.{db,storage,orchestrator}` + the adapters contract layer | tenant-scoped CRUD, transactions, DAG construction |
| `sceneapi/server/orchestrator/` | `sceneapi.server.db`, `sceneapi.server.workers.runner` | DAG, lease, scheduler, recipes, resume |
| `sceneapi/server/workers/` | `sceneapi.server.adapters` only | per-task lease + heartbeat; calls backend through the registry |
| `sceneapi/server/adapters/` | backend Protocols + registry only | no engine imports — engines ship in their own package |

A test (`tests/unit/test_app_starts.py`) enforces that importing
`sceneapi.server.main` does not pull in any engine library (pycolmap, torch,
cv2, segment_anything, ...). CI fails if any of those leak.

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

Most SfM engines mutate their working state (a SQLite DB, sparse
reconstruction directory, ...) in place. Reading those while the
worker writes them produces torn protobuf, partial JSON, and
sometimes SIGSEGV. The worker periodically copies the live
working state to `snapshots/.tmp_{seq}/` then `os.replace`s the
directory atomically; the API only ever serves data from sealed
`snapshots/{seq}/` dirs.

```{mermaid}
sequenceDiagram
    participant Worker
    participant Disk
    participant API
    participant Client

    Worker->>Disk: write live sparse reconstruction
    loop every 50 image registrations
        Worker->>Disk: copy live state to a temporary snapshot
        Worker->>Disk: write completion marker
        Worker->>Disk: atomically promote snapshot 42
        Worker->>API: emit snapshot available event
    end
    Client->>API: request sealed snapshot 42
    API->>Disk: serve immutable file
    API-->>Client: bytes
```

## Why the runtime version vector

SfM backends ship new builds frequently. A reconstruction cached
against backend SHA `abc` is not equivalent to one cached against
`def`, even if the spec is identical. Each cache key salts in the
backend's `runtime_version_id` — a freeform fingerprint string the
backend computes (typically rolled up from engine commit + auxiliary
library shas + CUDA arch + a deterministic seed). When a worker
upgrade swaps the backend or its underlying engine, cached output
invalidates automatically. sfmapi treats the string as opaque; the
backend defines what goes into it.

## Storage layout

```text
workspaces/{tenant_id}/
  blobs/{aa}/{sha256}                 # uploaded bytes (refcounted)
  _cache/s3/{bucket}/{key-hash}       # global S3 LRU
  projects/{pid}/datasets/{did}/
      manifest.json
      masks/{maskset_id}/...
  projects/{pid}/reconstructions/{rid}/
      database.db                     # backend-private; never read by the API
      sparse/{idx}/                   # live, backend-only
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
