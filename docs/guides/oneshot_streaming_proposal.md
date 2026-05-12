# One-Shot Streaming Endpoints — Design Proposal

**Status**: Proposal. Not implemented. Reviewed but not committed.

**Owner**: TBD.

**Companion**: `docs/guides/decisions.md` (decision register; this
proposal would add `P4`), `docs/guides/architecture.md` (current
resource-based flow), `app/api/v1/sfm_stages.py` (existing stage
endpoints — what we are *not* changing).

---

## The problem

The current API is **resource-based and persistent**. To get
features out of an image, you:

1. `POST /v1/projects` → project_id
2. `POST /v1/projects/{pid}/datasets` → dataset_id
3. `POST /v1/uploads` → upload_id
4. `PATCH /v1/uploads/{id}` × N (chunks)
5. `POST /v1/uploads/{id}:finalize` → blob_sha
6. `POST /v1/datasets/{did}/images` → image_id
7. `POST /v1/datasets/{did}/features` → 202 + job_id
8. Poll `GET /v1/jobs/{id}` until terminal
9. Read from sealed snapshot

Eight round-trips and persisted state at every step. That's the right
shape for a multi-stage SfM pipeline (`features → matches → verify →
map → ba`) where each stage needs the prior outputs and resume must
work — but it's overkill for the consumer who just wants "give me the
SIFT keypoints from this image right now."

The streaming use case:

- A client wants 2D features from one image with **no persistence**.
- It will not resubmit, it does not need resume, no other consumer
  will ever ask about this image again.
- Response time matters more than throughput.
- Bytes-in / typed-result-out should be one HTTP request.

## Why this can't be a flag on the existing flow

Several of sfmapi's architectural invariants are load-bearing for the
resource API and would break under streaming:

| Invariant | Resource API | Streaming | Conflict |
|---|---|---|---|
| Image addressed by `content_sha` | yes | no — bytes are ephemeral | An ephemeral image has no stable ID; subsequent stages can't reference it |
| `cache_key = H(inputs_hash, params_hash, rv)` for skip-if-already-ran | yes | no — would always cache-miss | Cache lookup costs more than the streaming work it would save |
| Sealed snapshots as immutable views | yes | no — no recon row | API never reads live state — but here there *is* no live state |
| Resume from `MappingInput` checkpoints | yes | no — single-stage only | Streaming can't span multiple stages |
| `Job.status` rollup via `_maybe_finalize_job` | yes | no — no Job row | Worker doesn't have a Job to finalize |

Forcing the streaming case through the resource API would create a
"phantom project / phantom dataset / phantom image" rows that exist
only to satisfy referential constraints — that's worse than a
separate namespace.

## Recommendation: separate `/v1/oneshot/...` namespace

A small, focused subset of stage endpoints under `/v1/oneshot/`. Each
takes image bytes in the request body, dispatches inline, returns
typed results in the response body. **No DB row, no persisted blob,
no Job, no sealed snapshot, no sequence number.**

### Wire shape

```http
POST /v1/oneshot/features
Content-Type: image/jpeg
[query: type=sift, max_num_features=8192, use_gpu=true]

<image bytes — same content as a finalized upload>
---
200 OK
Content-Type: application/json

{
  "schema_version": 1,
  "kind": "oneshot.features",
  "image": {"width": 4032, "height": 3024, "byte_size": 2147483},
  "features": {
    "type": "sift",
    "count": 4096,
    "keypoints": [[x, y, scale, angle], ...],   // or binary
    "descriptors_b64": "..."                    // base64-encoded float32
  },
  "runtime": {"backend": "<backend-name>", "ms": 312}
}
```

Response Content-Type can be:
- `application/json` (default — ergonomic, larger over wire).
- `application/x-sfm-features-oneshot-v1` (binary — small custom
  envelope mirroring the existing `points-binary` shape; magic +
  width + height + count + descriptor stride + descriptor bytes).
  Default to JSON; the binary form is a future optimization.

### Initial endpoints (Phase a)

Just one. Don't ship a kitchen sink:

| Route | Purpose | Notes |
|---|---|---|
| `POST /v1/oneshot/features` | Extract 2D features from one image. | Mirrors the parameter set of `FeaturesSpec` (type, max_num_features, use_gpu, seed, backend_options). |

### Phase b — `POST /v1/oneshot/localize` (added per user direction)

The single-frame-pose-against-existing-reconstruction case. Real
consumer flow: a tool (heritage doc viewer, AR overlay, robot
relocalization-from-known-map) wants pose against a static scan
without doing the upload+image+job+poll dance.

#### Wire shape

```http
POST /v1/oneshot/localize?recon_id=01HZRECON00000000000000000
Content-Type: image/jpeg
[query: type=sift, max_num_features=8192, ransac_max_error=12.0]

<image bytes>
---
200 OK
Content-Type: application/json

{
  "schema_version": 1,
  "kind": "oneshot.localize",
  "recon_id": "01HZRECON00000000000000000",
  "image": {"width": 4032, "height": 3024, "byte_size": 2147483},
  "result": {
    "success": true,
    "num_inliers": 142,
    "cam_from_world": {
      "rotation": {"w": ..., "x": ..., "y": ..., "z": ...},
      "translation": [x, y, z]
    },
    "inlier_matches": [[query_kp_idx, point3d_id], ...]
  },
  "runtime": {"backend": "<backend-name>", "ms": 287}
}
```

The shape mirrors `LocalizationResult` from `app/schemas/api/scene.py`
verbatim under the `result` key, so SDK consumers can re-use the
existing typed decoder.

#### Why this is in P4 and not the resource API

The resource-API equivalent (`POST /v1/reconstructions/{rid}/localize`)
already exists, takes a registered `blob_sha`, returns a 202 +
JobAcceptedResponse. End-to-end this requires:

1. `POST /v1/uploads` → upload_id
2. `PATCH /v1/uploads/{id}` × N
3. `POST /v1/uploads/{id}:finalize` → blob_sha
4. `POST /v1/reconstructions/{rid}/localize` { blob_sha } → 202 + job_id
5. Poll `GET /v1/jobs/{id}` until terminal
6. Decode the localize task's `outputs_ref` into `LocalizationResult`

Six round-trips. The `oneshot/localize` variant collapses to one.
Same backend call (`backend.localize_from_memory(query_image, recon)`),
no Job / Image / Blob / Upload row created.

#### Implementation delta on top of Phase a

- ~40 LOC in `app/api/v1/oneshot.py` — new route handler.
- ~50 LOC in `app/services/oneshot_service.py` — `localize_inline(body, recon_id, spec)`. Reuses `localize.run`'s materialization helpers
  but writes the query image to a tempfile instead of hardlinking from blob storage.
- ~30 LOC in `app/schemas/api/oneshot.py` — `OneShotLocalizeResponse` (reuses the existing `LocalizationResult` type via composition).
- 1 contract test + 1 typing guard.

**Phase a + b combined: ~9 hours, ~500 LOC.** Ship together if
both use cases are real today.

### Future endpoints (Phase c — defer until a real consumer asks)

| Route | Purpose | Notes |
|---|---|---|
| `POST /v1/oneshot/match` | Match two images submitted as multipart. | Body: `multipart/form-data` with two image parts + matcher spec. Returns inlier count + correspondences. Useful only for non-SLAM debugging — for real matching, use the resource API. |

`oneshot/match` deliberately keeps the **existing recon** as the
right-hand operand (matching against another ephemeral image without
prior context isn't a useful operation in classic SfM — you'd want
features-then-match anyway). Adding it later is fine but it doesn't
need to ship in Phase a.

### Non-goals

- **No `oneshot/map`, `oneshot/triangulate`, `oneshot/ba`, etc.** Those are inherently
  multi-image multi-stage operations. Forcing them into a single
  request would require streaming N images and N hours of compute
  through one HTTP connection — that's what the resource API exists
  for.
- **No streaming progress events.** One request, one response. If
  you want progress, use the resource API + SSE.
- **No persistence.** The endpoint never writes to the workspace.
  No blob in `BlobStore`, no `TempUploadStore` chunk file, no Image
  row, no Job row, no sealed snapshot.
- **No tenancy hooks beyond auth.** `current_tenant()` runs to
  enforce auth, but no tenant-scoped row is created. Quota
  enforcement happens at the request level (rate limit), not at
  the storage level.
- **No cache.** Two identical requests run twice. That's the
  trade for "no persistence" — symmetrical.

## Implementation sketch

### New route file

`app/api/v1/oneshot.py` (~60 LOC):
```python
@router.post("/features", response_model=OneShotFeaturesResponse)
async def extract_features_oneshot(
    request: Request,
    type: FeatureType = Query("sift"),
    max_num_features: int = Query(8192, ge=1, le=65536),
    use_gpu: bool = Query(True),
    seed: int = Query(0),
    tenant_id: str = Depends(current_tenant),  # auth only
) -> OneShotFeaturesResponse:
    body = await request.body()
    if len(body) > settings.oneshot_max_request_bytes:
        raise QuotaExceededError(...)
    spec = FeaturesSpec(type=type, max_num_features=max_num_features, ...)
    result = await asyncio.to_thread(
        oneshot_service.extract_features_inline, body, spec
    )
    return OneShotFeaturesResponse(...)
```

### New service module

`app/services/oneshot_service.py` (~80 LOC):
- `extract_features_inline(body: bytes, spec: FeaturesSpec) -> dict`
- Writes `body` to a `tempfile.NamedTemporaryFile` (the pycolmap
  binding wants a path), invokes the same backend method that
  `extract.run` uses, deletes the tempfile on return.
- The tempfile is the only "persistence" — it's gone before the
  response is constructed.

### New schema

`app/schemas/api/oneshot.py` (~40 LOC):
- `OneShotFeaturesResponse` — typed wire shape.
- Reuses `FeaturesSpec` enum constants where possible.

### Settings

Add `oneshot_max_request_bytes: int = 50 * 1024 * 1024` to
`app/core/config.py::Settings` so deployments can cap one-shot
request size separately from chunked uploads.

### Tests

- Unit: `oneshot_service.extract_features_inline` against a real
  small image (committed to `tests/fixtures/` — already done for
  the binary tests).
- Integration: live-server contract test under
  `tests/contract/` using the existing `live_ephemeral_server`
  fixture. Posts an image, asserts response shape, asserts no row
  appears in `Image` / `Blob` / `Job` tables (the no-persistence
  invariant).
- Conformance: a regression guard verifying the route advertises
  `response_model` (so SDK codegen produces typed bindings).

## Cost estimate

| Item | LOC | Time |
|---|---|---|
| `app/api/v1/oneshot.py` | ~60 | 1h |
| `app/services/oneshot_service.py` | ~80 | 1.5h |
| `app/schemas/api/oneshot.py` | ~40 | 0.5h |
| Settings + router registration in `main.py` | ~10 | 0.25h |
| Unit + integration + conformance tests | ~150 | 2h |
| SDK regen + contract fixture | ~0 (auto) | 0.5h |
| Decision register update | ~10 | 0.25h |

**Total: ~6 hours of careful work, ~350 LOC.**

## Decision tree

```
Is "give me features from one image, right now" a real consumer use?
├─ YES → Approve Phase a (POST /v1/oneshot/features only).
│        Ship Phase b (oneshot/match, oneshot/localize) only after a
│        real consumer asks.
└─ NO  → Reject. The resource API + ephemeral mode + InlineQueue
         already cover "synchronous request → typed response with
         no background workers" for consumers willing to do the
         8-step setup.
```

## Trade-offs explicit

**Wins**:
- Single round-trip for the most common SDK demo. Lowers the
  on-ramp from "8 calls" to "1 call".
- Cleaner fit for a few real flows: per-frame feature extraction
  for a video preview UI, ad-hoc image debugging, content-moderation
  pipelines that just want feature density.
- Forces a discipline boundary: the namespace makes it impossible
  to accidentally mix "ephemeral one-shot" with "persistent
  resource-based" semantics.

**Losses**:
- One more API surface to maintain. Conformance tests, contract
  fixtures, SDK ergonomics all gain a small footprint each.
- Encourages consumers to skip the resource API for use cases that
  actually do need persistence (e.g. they call
  `/v1/oneshot/features` 10,000 times for a video, when a single
  dataset + one features stage would have cached the runtime
  version setup, deduplicated identical frames via blob sha, etc.).
  Mitigated by documentation: the endpoint's docstring should say
  "for one-image-or-go-home — for batch / video, use the resource
  API."

## Recommendation: approve Phase a

The use case is real (consumer-facing SDK demos, ad-hoc image
debugging, video-frame previews), the implementation is contained
(~6h, ~350 LOC, one new namespace), and the architectural
invariants are preserved by keeping the namespace separate from
the resource API. **Approve `POST /v1/oneshot/features` only;
revisit Phase b when a consumer asks.**

Add as `P4` in `docs/guides/decisions.md`:

```
| P4 | One-shot streaming features endpoint at `POST /v1/oneshot/features`. | Ready to ship Phase a (~6h). | docs/guides/oneshot_streaming_proposal.md | Single user `OK` |
```
