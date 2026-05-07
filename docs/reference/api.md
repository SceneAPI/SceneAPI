# REST API reference

All paths are prefixed with `/v1` unless noted. The **canonical**,
always-accurate reference is the live OpenAPI spec rendered as
Swagger UI on the [OpenAPI](openapi.md) page — this document gives
the resource-shape overview and the most-used endpoint groups for
quick orientation.

## Resource model

```{mermaid}
flowchart LR
    Tenant["Tenant"] --> Project["Project"]
    Project --> Dataset["Dataset"]
    Project --> Job["Job"]
    Project --> Reconstruction["Reconstruction"]
    Dataset --> Image["Image"]
    Reconstruction --> SubModel["SubModel"]
    Reconstruction --> SealedSnapshot["Sealed snapshot"]
    Image -.-> Blob
    Upload["Upload"] --> Blob["Blob"]
    Job --> Task["Task"]
```

| Resource | Lifetime | Owns | Created via | Notes |
|---|---|---|---|---|
| `Project` | persistent | Datasets / Reconstructions / Jobs | `POST /v1/projects` | Top-level workspace |
| `Dataset` | persistent | Images, ImageSource | `POST /v1/projects/{pid}/datasets` | Discriminated `source.kind`: `upload` / `local` / `s3` |
| `Image` | persistent | (links a `Blob` or rel_path) | `POST /v1/datasets/{did}/images`, `:batchCreate` | Per-image EXIF / thumbnail / bytes endpoints |
| `Upload` | TTL (24h default) | one `Blob` after finalize | `POST /v1/uploads` | Chunked: `PATCH` chunks, `POST :finalize` to seal |
| `Blob` | content-addressed | none (deduplicated) | implicit from upload finalize | sha256-keyed; reference-counted |
| `Job` | persistent | Tasks, ProgressEvents | every job-submitting `POST` returns 202 + `Location` | LRO; `:cancel` / `:resume` colon verbs |
| `Task` | persistent | (worker result via `outputs_ref`) | created by orchestrator from DAG | Status enum: `pending|running|succeeded|failed|cancelled|cancelled_dirty|skipped` |
| `Reconstruction` | persistent | SubModels + SealedSnapshots | implicit from `:features` / pipelines | Status mirrors driving Job |
| `SubModel` | persistent | one sealed dir per model | implicit (one per pycolmap component) | Indexed by `idx`; sealed paths in `sparse/N/` |
| `SealedSnapshot` | append-only | files (cameras, images, points.bin, ...) | implicit on phase boundary | Reads via `/snapshots/{seq}/{name}`; `points.bin` is the binary points wire format |
| `ApiKey` | persistent | tenant binding | `POST /v1/admin/api-keys` (admin) | Raw key returned once at issue time |

## Conventions

- **Auth**: see [authentication](auth.md) — default `auth_mode=none`
  for dev; `auth_mode=api_key` enables `Authorization: Bearer <key>`.
- **Errors**: `application/problem+json` per [RFC 7807][rfc7807] —
  see [errors](errors.md). Typed `errors[]` array on 422.
- **IDs**: 26-char ULIDs (Crockford base32, sortable, timestamp-prefixed).
- **Timestamps**: ISO-8601 / RFC 3339 in UTC.
- **Pagination** (AIP-158): keyset via `?page_token=` + `?page_size=`.
  Responses include `next_page_token` (`null` ends the cursor).
  Page tokens are opaque — clients MUST NOT parse them.
- **Long-running ops**: `POST` returns `202 Accepted` with
  `JobAcceptedResponse{job_id, task_ids[], …}` and a
  `Location: /v1/jobs/{id}` header. Cancel via `POST
  /v1/jobs/{jid}:cancel` (AIP-136 colon verb), not `DELETE`.
- **Idempotency**: `Idempotency-Key` header on `POST /v1/uploads`
  (replay-safe).
- **Caching**: sealed snapshot files emit strong `ETag` +
  `Cache-Control: public, max-age=31536000, immutable`.
- **SSE / WebSocket**: `GET /v1/jobs/{id}/events` (SSE) or
  `GET /ws/v1/jobs/{id}` (WS). Both honor `Last-Event-ID` resume.

[rfc7807]: https://www.rfc-editor.org/rfc/rfc7807

## Discovery & meta

| Method | Path | Purpose |
|---|---|---|
| GET | `/healthz` | Liveness probe |
| GET | `/readyz` | Readiness — DB + queue checks |
| GET | `/version` | sfmapi + backend SHAs |
| GET | `/spec` | Spec discovery envelope |
| GET | `/v1/capabilities` | Backend feature flags (dot-notated names) |
| GET | `/openapi.json` | OpenAPI 3.1 document |
| GET | `/metrics` | Prometheus exposition |

## Projects

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/v1/projects` | `{name, description?}` | `Project` |
| GET | `/v1/projects` | `?page_token=&page_size=` | `Page<Project>` |
| GET | `/v1/projects/{pid}` | — | `Project` |
| PATCH | `/v1/projects/{pid}` | `ProjectPatch` + optional `?update_mask=` | `Project` |
| DELETE | `/v1/projects/{pid}` | — | 204 |
| POST | `/v1/projects/{pid}/datasets:from_video` | `VideoFramesRequest` | 202 + `JobAccepted` |
| POST | `/v1/projects/{pid}/datasets:import_kapture` | `KaptureImportRequest` | 202 + `JobAccepted` |

PATCH accepts an optional AIP-161 `update_mask` query parameter. Mask
paths are comma-separated, body-relative, and must also be present in
the JSON body; without a mask, sfmapi applies the body fields that are
present.

## Uploads (chunked)

| Method | Path | Body / Headers | Returns |
|---|---|---|---|
| POST | `/v1/uploads` | `{expected_size, content_type?, expected_sha?}` + `Idempotency-Key` | `Upload` |
| GET | `/v1/uploads/{uid}` | — | `Upload` |
| PATCH | `/v1/uploads/{uid}` | raw chunk + `Content-Range: bytes A-B/T` | `Upload` |
| POST | `/v1/uploads/{uid}:finalize` | `{}` (or `X-Content-SHA256` header) | `Upload` |

## Datasets & images

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/v1/projects/{pid}/datasets` | `{name, source: SourceSpec, camera_model, intrinsics_mode, is_spherical, rig_config?, respect_exif_orientation}` | `Dataset` |
| GET | `/v1/projects/{pid}/datasets` | `?page_token=&page_size=` | `Page<Dataset>` |
| GET | `/v1/projects/{pid}/datasets/{did}` | — | `Dataset` |
| PATCH | `/v1/projects/{pid}/datasets/{did}` | `DatasetPatch` + optional `?update_mask=` | `Dataset` |
| DELETE | `/v1/projects/{pid}/datasets/{did}` | — | 204 |
| POST | `/v1/datasets/{did}:render_cubemap` | `?face_size=` | 202 + `JobAccepted` |
| POST | `/v1/datasets/{did}/images` | `ImageCreate` | `Image` |
| POST | `/v1/datasets/{did}/images:batchCreate` | `BatchCreateImagesRequest{requests[]}` | `BatchCreateImagesResponse{images[]}` |
| GET | `/v1/datasets/{did}/images` | `?page_token=&page_size=` | `Page<Image>` |
| DELETE | `/v1/images/{iid}` | — | 204 |
| DELETE | `/v1/datasets/{did}/images/{name}` | legacy label-addressed delete | 204 |
| GET | `/v1/images/{iid}` | — | `Image` |
| GET | `/v1/images/{iid}/bytes` | `If-None-Match` (optional) | image bytes |
| GET | `/v1/images/{iid}/thumbnail` | `?size=N` | JPEG |
| GET | `/v1/images/{iid}/exif` | — | `ImageExifResponse` |
| GET / PUT / DELETE | `/v1/images/{iid}/pose_prior` | `PosePrior` | `PosePrior \| null` |
| GET / PUT | `/v1/datasets/{did}/pose_priors` | `PosePriorsBulkRequest` | `PosePriorsBulkResponse` |

## SfM stages (single-task jobs)

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/v1/datasets/{did}/features` | `{spec: FeaturesSpec}` | 202 + `JobAccepted` |
| POST | `/v1/datasets/{did}/matches` | `{pairs: PairsSpec, matcher: MatcherSpec}` | 202 + `JobAccepted` |
| POST | `/v1/datasets/{did}/verify` | `{spec: VerifySpec}` | 202 + `JobAccepted` |

## Pipelines (recipe sugar)

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/v1/projects/{pid}/pipelines/{recipe}` | `{dataset_id, features?, pairs?, matcher?, verify?, spec: PipelineSpec}` | 202 + `JobAccepted` |

`recipe ∈ {incremental, global, hierarchical, spherical}` and
`spec.kind` MUST match.

## Reconstructions / submodels / snapshots

| Method | Path | Returns |
|---|---|---|
| GET | `/v1/reconstructions/{rid}` | `Reconstruction` |
| GET | `/v1/reconstructions/{rid}/submodels` | `Page<SubModel>` |
| GET | `/v1/submodels/{smid}` | `SubModel` |
| GET | `/v1/reconstructions/{rid}/snapshots` | `SnapshotListResponse` |
| GET | `/v1/reconstructions/{rid}/snapshots/{seq}/{name}` | file bytes |
| GET | `/v1/reconstructions/{rid}/two_view_geometries.json` | JSON |
| GET | `/v1/reconstructions/{rid}/correspondence_graph.json` | JSON |
| POST | `/v1/reconstructions:merge` | `MergeRequest` | 202 + `JobAccepted` |

`{name}` is one of `cameras.json | images.json | rigs.json |
frames.json | pose_graph.json | summary.json | points.bin |
points_preview.bin | tiles/index.json | dense/index.json |
dense/fused.bin`.

## Reconstruction-level stages (LRO)

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/v1/reconstructions/{rid}/localize` | `LocalizationRequest{blob_sha, sift?}` | 202 + `JobAccepted` |
| POST | `/v1/reconstructions/{rid}/georegister` | `Sim3` | 202 + `JobAccepted` |
| POST | `/v1/reconstructions/{rid}/mesh` | `MeshRequest{method, options?}` | 202 + `JobAccepted` |
| POST | `/v1/reconstructions/{rid}/dense` | — | 202 + `JobAccepted` |
| POST | `/v1/reconstructions/{rid}:to_cubemap` | — | 202 + `JobAccepted` |

## Similarity

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/v1/datasets/{did}/similarity` | `?image_id=&k=&strategy=&include_self=` | `SimilarityQueryResponse` |
| POST | `/v1/datasets/{did}/similarity:build` | `?strategy=dhash\|vlad&force=` | 200 (`dhash`) or 202 (`vlad`) |

## One-shot (bytes-in / typed-result-out)

For "right now" use cases that don't need a Project/Dataset row.

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/v1/oneshot/features` | image bytes (`Content-Type: image/...`) | `OneShotFeaturesResponse` |
| POST | `/v1/oneshot/localize` | image bytes + `?recon_id=` | `OneShotLocalizeResponse` |

Bytes are tempfile'd then deleted; no DB row is created. Capped at
`SFMAPI_ONESHOT_MAX_REQUEST_BYTES` (50 MiB default).

## Jobs

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/v1/jobs` | `?page_token=&page_size=&status=` | `Page<JobOut>` |
| GET | `/v1/jobs/{jid}` | — | `JobDetail` |
| POST | `/v1/jobs/{jid}:cancel` | `?force=true` (optional) | `JobOut` |
| POST | `/v1/jobs/{jid}:resume` | — | 202 + `JobOut` |
| GET | `/v1/jobs/{jid}/events` | `Last-Event-ID` (optional) | SSE stream of `ProgressEvent` |
| GET | `/ws/v1/jobs/{jid}` | WebSocket upgrade | bidirectional events |

`?status=` filters on the closed `JobStatus` set: `pending |
running | succeeded | failed | cancelled | cancelled_dirty`.

## Admin

`/v1/admin/api-keys` are operator routes. They are not tenant-scoped
and are not protected by sfmapi's tenant API-key dependency, so
production deployments must restrict them with an external admin-only
control-plane layer.

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/v1/admin/api-keys` | `{tenant_id, name?}` | `IssueKeyResponse` (`raw_key` returned **once**) |
| GET | `/v1/admin/api-keys` | — | `[ApiKeyOut]` |
| DELETE | `/v1/admin/api-keys/{kid}` | — | `ApiKeyOut` (revoked) |

## ProgressEvent

```{eval-rst}
.. automodule:: app.schemas.progress_event
   :members:
   :no-index:
```

## Pipeline specs

```{eval-rst}
.. automodule:: app.schemas.pipeline_spec
   :members:
   :no-index:
```

## Binary points format

`Content-Type: application/x-sfm-points-v1`. Fixed 32-byte header,
26 bytes per point.

```{eval-rst}
.. automodule:: app.schemas.points_binary
   :members:
   :no-index:
```

Records are written in ascending `point3d_id` order so HTTP `Range`
requests treat the file as a fixed-stride array. `points_preview.bin`
is the same format, decimated.
