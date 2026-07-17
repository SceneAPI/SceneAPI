# Sealed Snapshots on Object Storage — Design Proposal

**Status**: Proposal. Not implemented. Reviewed but not committed.

**Owner**: TBD.

**Companion**: `docs/guides/architecture.md` (sealed-snapshot
contract), `app/storage/snapshots.py` (current FS writer),
`app/storage/blobs.py::S3BlobStore` (existing S3 backend),
`docs/phases/phase_5_resume_tenancy_s3_obs.md` (Phase 5 plan).

---

## The invariant we depend on

Sealed snapshots are the entire foundation of sfmapi's
"API never reads live state" rule. The contract is:

1. The worker writes mutable state (`database.db`, `sparse/`,
   in-progress events) freely.
2. Periodically the worker **seals** a copy into
   `snapshots/{seq:08d}/` — atomic publish, immutable from then on.
3. The API only ever reads sealed dirs. No race, no half-written
   files, no partial JSON.

The current implementation (`app/storage/snapshots.py::SnapshotStore.seal`)
relies on **POSIX `os.replace(tmp, target)`** — atomic when both
paths share a filesystem. That's the entire correctness story.

## What breaks on S3

S3 is **not a filesystem**. The atomicity primitive doesn't exist:

| FS primitive | S3 equivalent |
|---|---|
| `mkdir snapshots/.tmp_5` | (no-op — S3 has no real directories) |
| Write 12 files into `.tmp_5/` | 12 separate `PUT` requests, each atomic individually |
| `mv .tmp_5 5` (atomic dir rename) | **Does not exist.** Closest is `CopyObject` per file × 12 + `DeleteObject` × 12 — not atomic, not all-or-nothing |
| `.complete` marker | Single `PUT` is atomic, but consumers might read other files **before** the marker arrives |

The fundamental problem: S3 has per-object atomicity, not
per-prefix-collection atomicity. A consumer issuing
`GET /snapshots/5/cameras.json` could land on any of these states
during a multi-object publish:

- **0/12 files visible** — 404, retryable.
- **1-11/12 files visible** — partial read, **silent corruption** if
  the consumer doesn't notice. This is the failure mode we MUST
  prevent.
- **12/12 files visible** — fine.
- **`.complete` marker visible without all files** — fine if the
  consumer guards on the marker, but the marker is itself just a
  PUT and provides no transactional ordering across the others.

## Three candidate protocols

### Option A: Manifest-pointer (recommended)

The seal becomes one logical write: a single `manifest.json` object
that contains hashes + S3 keys for every file in the snapshot. The
manifest is the **only** thing the snapshot index points at; files
underneath are content-addressed and shared across snapshots.

Wire layout:
```
s3://bucket/recons/{rid}/snapshots/manifests/{seq:08d}.json
s3://bucket/recons/{rid}/blobs/{sha[:2]}/{sha}        # content-addressed
```

Manifest shape:
```json
{
  "seq": 5,
  "schema_version": 1,
  "created_at": "2026-05-05T...",
  "summary": {...},
  "files": {
    "cameras.json": {"sha256": "abc...", "size": 1234},
    "images.json":  {"sha256": "def...", "size": 9876},
    "points.bin":   {"sha256": "ghi...", "size": 4567},
    ...
  }
}
```

Seal protocol:
1. For each file in the live `sparse/`: hash it, `PUT` to
   `blobs/{sha[:2]}/{sha}` (skip if exists via `HeadObject`).
2. Build the manifest dict.
3. `PUT manifests/{seq:08d}.json` with `If-None-Match: "*"` to
   guarantee no overwrite.
4. Update `manifests/latest.txt` to `{seq:08d}`.

Read protocol:
1. `GET manifests/{seq:08d}.json` — single atomic read.
2. For each filename in `files`: `GET blobs/{sha[:2]}/{sha}` and
   verify hash on read.
3. If the manifest read succeeds, every file in it is guaranteed
   present (because the manifest is only `PUT` after every blob is
   uploaded).

**Atomicity invariant**: a snapshot **exists** iff its manifest
exists. The blobs underneath might exist before the manifest does
— that's fine, they're content-addressed and immortal.

**Advantages**:
- Single-object atomicity is enough — no multi-key transaction.
- Cross-snapshot dedup is **free** because blobs are
  content-addressed. Two snapshots that share `cameras.json` (no
  changes between seals) reference the same blob.
- Same protocol works for FS too — could replace the current
  `os.replace`-based writer, unifying the two backends.
- Manifest is small (~few KB) — fits in any cache, fast to read.

**Disadvantages**:
- One extra round-trip per file on first seal (HeadObject before
  PutObject). Mitigated by parallel head + `If-None-Match: "*"`
  on the put — fail-fast on existing means the head is optional.
- Existing FS readers that walk a directory listing need rewriting
  to consume manifests. That's `app/api/v1/reconstructions.py::list_snapshots`
  + `read_snapshot_file` — manageable scope.

### Option B: Multipart-as-snapshot

Bundle every snapshot file into a single tar/zip, upload as a
multipart upload, completion is atomic.

**Advantages**:
- Single object per snapshot. Simple mental model.
- Atomic by construction — multipart `CompleteMultipartUpload` is
  all-or-nothing.

**Disadvantages**:
- **Kills sparse reads.** Can't `GET /snapshots/5/cameras.json`
  cheaply — need to range-read into the archive, decode, extract.
- **Kills cross-snapshot dedup.** Each tar is independent; even
  unchanged files re-upload.
- **Kills HTTP caching.** ETag is for the whole tar, not the
  inner file. `If-None-Match` on `cameras.json` doesn't work.
- Misuse of multipart — multipart is for *single* large objects.

Reject.

### Option C: Copy-then-delete with retry

Write all files individually, then rename via `CopyObject` from
`.tmp_5/*` to `5/*` and delete the tmp originals. Use a manifest
to know what to copy.

**Advantages**:
- Doesn't require content-addressed blobs.

**Disadvantages**:
- Not atomic — copy is per-object. A reader during the copy
  phase sees partial state.
- Doubles every write (PUT to tmp + CopyObject to final).
- Wastes the dedup property entirely.
- Has all the disadvantages of Option A with none of the
  advantages.

Reject.

## Recommendation: Option A (manifest-pointer + content-addressed blobs)

The atomicity invariant collapses to a single object — a manifest.
The protocol generalizes to FS (same manifest layout, FS just
happens to be storage; `os.replace` becomes irrelevant). Cross-
snapshot dedup is free. HTTP caching keeps working at the per-file
level because each blob has a stable `ETag` (the sha itself).

## Migration plan (concrete)

This is a wire-protocol change but only on the **internal** sealed-
snapshot layer — the API surface (`GET /v1/reconstructions/{rid}/snapshots/{seq}/{name}`)
stays identical because the API can resolve `name → sha` via the
manifest before fetching from blob storage.

1. **Phase a — backend abstraction.** Add a `SnapshotStore`
   Protocol matching the existing FS class. Provide
   `FSSnapshotStore` (current behavior, no-op rename) and
   `S3SnapshotStore` (manifest-pointer protocol). `get_snapshot_store()`
   factory keyed on `settings.snapshot_backend` (`fs` | `s3`).
   ~250 LOC, zero API changes.

2. **Phase b — FS unification (optional).** Migrate `FSSnapshotStore`
   to also use the manifest-pointer protocol. Eliminates the
   `os.replace`-vs-S3 split. ~100 LOC; existing snapshots stay
   readable via a one-shot migration script that builds manifests
   from existing dir contents.

3. **Phase c — API layer routes.** `read_snapshot_file` reads
   manifest, looks up file's sha, fetches blob. Adds a per-process
   manifest cache (LRU keyed on `(recon_id, seq)` — manifests are
   immutable so cache invalidation is trivial). ~80 LOC.

4. **Phase d — observation sidecars + dense + tiles.** Same
   pattern applied to `observations_by_image.json`, `dense/...`,
   `tiles/*.bin`. Each is just another file in the manifest.

**Invariants preserved across the migration**:
- Sealed snapshots remain immutable.
- API never reads live state.
- ETags remain stable per content-addressed blob.
- Sequence numbers remain monotonic + zero-padded.
- The "manifest exists ⇔ snapshot exists" rule replaces
  ".complete file exists ⇔ snapshot exists".

## What this proposal explicitly does NOT do

- **Does not** change the API surface visible to consumers. SDK
  contracts and contract-test fixtures stay valid.
- **Does not** depend on the Postgres-only commit.
- **Does not** depend on RLS work.
- **Does not** affect the `pycolmap.MappingInput` checkpoint
  protocol or the `Task.cache_key` orchestrator behavior — those
  are separate primitives (see `resume_unification_proposal.md`).

## Estimated cost

| Phase | LOC delta | New files | Tests | Time |
|---|---|---|---|---|
| a — Protocol + 2 impls + factory | ~250 | 2 | unit + integration | ~6h |
| b — FS unification (optional) | ~100 | 0 | migrate fixtures | ~3h |
| c — API layer + manifest cache | ~80 | 1 (cache) | route tests | ~3h |
| d — Sidecars + dense + tiles | ~150 | 0 | per-feature | ~4h |

**Total: ~16 hours of careful work.** Not 16 hours of typing —
each phase has real test surface and the existing live-server
contract layer needs to stay green throughout.

## Decision — recommend approving for Phase a only

Phases a + c are required for any real S3 sealed-snapshot story —
those should land together. Phase b (FS unification) is purely a
cleanup; defer until after S3 is proven in production. Phase d
(sidecar formats) follows the same pattern and can be done
incrementally per feature.

The contract test layer already validates wire shapes against
recorded fixtures; the migration risk is the **internal** invariant
"sealed snapshot is durable + immutable", which the existing
integration tests for `SnapshotStore` cover. Add an S3-specific
integration test using a `moto`-stubbed S3 (already in the test
deps for `S3BlobStore` tests) to round-trip a manifest-pointer
snapshot end-to-end before approving Phase a as merged.
