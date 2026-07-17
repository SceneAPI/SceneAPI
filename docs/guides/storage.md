# Storage

Three orthogonal layers, each with a distinct responsibility:

| Layer | What it owns | Module |
|---|---|---|
| **Blobs** | Content-addressed immutable bytes for uploaded images | `sfmapi.server.storage.blobs` |
| **ImageSource** | Logical reference to *where* bytes live (upload / local / S3) | `sfmapi.server.sources.*` |
| **Materialization** | Per-job realization of an `ImageSource` to a real path the backend can read | worker-only |

## Blob store

```text
<blob_root>/<sha[:2]>/<sha>
```

Writes go through `BlobStore.put_stream()` — atomic via `os.replace`
from a temp file. Reads stream chunked. `Blob.refcount` (in the DB)
tracks how many entities (images, masks, model artifacts) reference
the blob; lifecycle is GC'd when refcount → 0.

```{eval-rst}
.. autoclass:: sfmapi.server.storage.blobs.BlobStore
   :members:
   :no-index:
```

## Image sources

Three implementations behind a single contract:

```{eval-rst}
.. autoclass:: sfmapi.server.sources.upload.UploadSource
   :members:
   :no-index:

.. autoclass:: sfmapi.server.sources.local.LocalPathSource
   :members:
   :no-index:

.. autoclass:: sfmapi.server.sources.s3.S3Source
   :members:
   :no-index:
```

### Local path: no copy, no symlink

`LocalPathSource` references the user's directory directly — the
backend gets pointed at the user's path. To detect "user mutated their dir
under us," we record a fingerprint of every file:
`{path, size, mtime_ns, sample_hash(head/mid/tail 1MiB)}`. Cheap,
deterministic, fixed-cost regardless of file size.

### S3: lazy download to a global LRU cache

`S3Source.materialize()` lists objects (filtered by extension), checks
the LRU cache keyed by `(bucket, key, etag)`, downloads any cache
miss. Cache is **shared across tenants** because content is addressed
by ETag, not by tenant prefix.

```{eval-rst}
.. autoclass:: sfmapi.server.storage.s3_cache.S3Cache
   :members:
   :no-index:
```

LRU eviction:

```python
cache = S3Cache()
cache.evict_to(max_bytes=10 * 1024**3)   # 10 GiB budget
```

Eviction order is by `meta.json` mtime (touched on every `lookup()`),
so MRU entries survive.

## Sealed snapshots

The worker's only safe handoff to the API is a sealed snapshot dir.
The protocol:

1. Write to `snapshots/.tmp_{seq}/`
2. Write `.complete` marker last
3. `os.replace(tmp, snapshots/{seq})` — atomic rename
4. Update `latest` text file (also via tmp+rename)

Readers list `snapshots/*/` and ignore any dir without a `.complete`
file. The API never opens a non-sealed file, ever.

```{eval-rst}
.. autoclass:: sfmapi.server.storage.snapshots.SnapshotStore
   :members:
   :no-index:
```

## MappingInput checkpoints

For incremental SfM resume, the worker writes
`MappingInput.save()` payloads under
`jobs/{job_id}/checkpoints/{seq}.pcmapin` every N image registrations.
On a re-run / resume, the worker calls
`pipeline.set_mapping_input(MappingInput.load(latest))` so the
expensive setup work isn't repeated.

```{eval-rst}
.. automodule:: sfmapi.server.storage.mapping_input
   :members:
   :no-index:
```

## GC

```{eval-rst}
.. autofunction:: sfmapi.server.storage.workspace.gc_completed_jobs
   :no-index:
```

Drops `dense → snapshots → sparse` per-job in that order, skips
pinned jobs (`Job.pinned=true`), and never touches a job's
`manifest.json`. Reconstruction-level artifacts are NOT touched by job
GC because reconstructions can outlive the job that produced them.
