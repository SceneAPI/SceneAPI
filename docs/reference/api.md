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
  Documented snapshot/navigation-index endpoints may use a compact
  sequence-index envelope instead of `Page<T>`.
- **Long-running ops**: `POST` returns `202 Accepted` with
  `JobAcceptedResponse{job_id, task_ids[], …}` and a
  `Location: /v1/jobs/{id}` header. Cancel via `POST
  /v1/jobs/{jid}:cancel` (AIP-136 colon verb), not `DELETE`.
  Poll `/v1/jobs/{jid}` for lifecycle state, `/v1/jobs/{jid}/progress`
  for dashboard-friendly progress, or `/v1/jobs/{jid}/events` for the
  full event stream.
- **Capabilities vs actions**: `/v1/capabilities` advertises portable
  sfmapi feature flags only. Backend-native commands live in
  `/v1/backend/actions`; action ids are stable dot-namespaced strings.
  Treat action ids as opaque and URL-encode them when building path
  requests.
- **Portable vs backend-specific options**: stage specs keep portable
  knobs at the top level. Provider-specific knobs go in
  `backend_options` and are discoverable from
  `/v1/backend/config-schemas`.
- **Plugin hub**: install and enable backend plugins with the
  `sceneapi plugins ...` CLI or `/v1/admin/plugins...` operator routes.
  Public SfM job APIs never install plugins implicitly; HTTP execution
  requires `allow_unsafe_execution=true`.
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
| GET | `/v1/backend` | Active backend identity, runtime versions, action links, and config-schema links |
| GET | `/v1/backend/providers` | Enabled providers discovered from installed plugins |
| GET | `/v1/backend/routing` | Provider priority and default routing-profile state |
| GET | `/v1/camera-models` | Portable camera model parameter layouts |
| GET | `/openapi.json` | OpenAPI 3.1 document |
| GET | `/metrics` | Prometheus exposition |

## Backend actions

Backend actions expose backend-native tools, such as COLMAP or OpenMVG
commands, without turning those tool names into portable sfmapi
capabilities.

| Method | Path | Body / Query | Returns |
|---|---|---|---|
| GET | `/v1/backend` | `?provider=` | `BackendOut` |
| GET | `/v1/backend/actions` | `?page_token=&page_size=&include_schemas=false&provider=` | `Page<BackendAction>` |
| GET | `/v1/backend/actions/{action_id}` | `?provider=` | `BackendAction` with schemas |
| POST | `/v1/backend/actions/{action_id}:validate` | `{provider?, inputs}` | `BackendActionValidateResponse` |
| POST | `/v1/backend/actions/{action_id}:run` | `{project_id, provider?, inputs}` | 202 + `JobAcceptedResponse` |
| GET | `/v1/backend/config-schemas` | `?page_token=&page_size=&include_schemas=true&provider=` | `Page<BackendConfigSchema>` |
| GET | `/v1/backend/config-schemas/{config_id}` | `?provider=` | `BackendConfigSchema` |
| GET | `/v1/backend/artifact-contracts` | `?page_token=&page_size=&provider=` | `Page<BackendArtifactContract>` |
| GET | `/v1/backend/artifact-contracts/{contract_id}` | `?provider=` | `BackendArtifactContract` |
| GET | `/v1/backend/providers` | `?page_token=&page_size=` | `Page<Provider>` |
| GET | `/v1/backend/routing` | - | `RoutingOut` |

`GET /v1/backend/actions` omits `input_schema` and `output_schema` by
default so catalog reads stay small. Pass `include_schemas=true`, or
read one action, when a UI needs form fields. `:run` enqueues a normal
`backend_action` job, returns `Location: /v1/jobs/{id}`, and includes
optional `action_id`, `backend`, and `provider` fields in the
accepted-job body. Pass `provider` when inspecting or running a
backend installed through sfm_hub without making it the process-wide
`SCENEAPI_BACKEND`.
When `SCENEAPI_MCP_MODE=local` or `SCENEAPI_MCP_ENABLED=true` mounts MCP
into the API process, `GET /v1/backend` also advertises `_links.mcp`
and `_links.mcp_status`.

Backend config schemas describe valid keys for `backend_options` on
portable stage specs. They are scoped by `stage`, optional
`capability`, and optional `provider`. sfmapi validates unknown keys
and basic JSON types before queuing a job when the active backend
or selected provider backend publishes a matching schema; otherwise
the options pass through and
the backend validates them.

Backend artifact contracts describe the portable artifact kinds and
format ids a stage accepts and emits. Core `sfmapi.*.v1` formats are
stable interchange contracts; backend-native formats remain
namespaced extensions such as `colmap.features.database.v1` or
`hloc.features.h5.v1`. Conversions are explicit metadata on the
contract and should declare whether they are lossless.

Provider discovery is driven by `sfm_hub` plugin manifests plus local
install state. A clean sfmapi install can return an empty provider
page and still run the configured backend directly. Once multiple
enabled providers expose the same portable capability, sfmapi requires
either a request-level `provider` or a project, workspace, default, or
priority routing rule.

Example feature extraction request:

```json
{
  "spec": {
    "type": "sift",
    "max_num_features": 8192,
    "backend_options": {
      "SiftExtraction.peak_threshold": 0.01
    }
  }
}
```

## Projects

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/v1/projects` | `{name, description?}` | `Project` |
| GET | `/v1/projects` | `?page_token=&page_size=` | `Page<Project>` |
| GET | `/v1/projects/{pid}` | — | `Project` |
| PATCH | `/v1/projects/{pid}` | `ProjectPatch` + optional `?update_mask=` | `Project` |
| DELETE | `/v1/projects/{pid}` | — | 204 |
| POST | `/v1/projects/{pid}/datasets:fromVideo` | `VideoFramesRequest` | 202 + `JobAccepted` |
| POST | `/v1/projects/{pid}/datasets:importKapture` | `KaptureImportRequest` | 202 + `JobAccepted` |
| POST | `/v1/projects/{pid}/datasets:fromArchive` | `ArchiveImportRequest` | 202 + `JobAccepted` |

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
| POST | `/v1/datasets/{did}:renderCubemap` | `CubemapProjectionRequest` or `?face_size=` | 202 + `JobAccepted` |
| POST | `/v1/datasets/{did}:renderEquirectangular` | `EquirectangularProjectionRequest` | 202 + `JobAccepted` |
| POST | `/v1/datasets/{did}:renderPerspective` | `PerspectiveProjectionRequest` | 202 + `JobAccepted` |
| POST | `/v1/datasets/{did}:projectImages` | `ProjectionJobRequest` | 202 + `JobAccepted` |
| POST | `/v1/datasets/{did}/images` | `ImageCreate` | `Image` |
| POST | `/v1/datasets/{did}/images:batchCreate` | `BatchCreateImagesRequest{requests[]}` | `BatchCreateImagesResponse{images[]}` |
| GET | `/v1/datasets/{did}/images` | `?page_token=&page_size=` | `Page<Image>` |
| DELETE | `/v1/images/{iid}` | — | 204 |
| DELETE | `/v1/datasets/{did}/images/{name}` | legacy label-addressed delete | 204 |
| GET | `/v1/images/{iid}` | — | `Image` |
| GET | `/v1/images/{iid}/bytes` | `If-None-Match` (optional) | image bytes |
| GET | `/v1/images/{iid}/thumbnail` | `?size=N` | JPEG; requires the optional image-processing extra |
| GET | `/v1/images/{iid}/exif` | — | `ImageExifResponse` |
| GET / PUT / DELETE | `/v1/images/{iid}/pose_prior` | `PosePrior` | `PosePrior \| null` |
| GET / PUT | `/v1/datasets/{did}/pose_priors` | `PosePriorsBulkRequest` | `PosePriorsBulkResponse` |

Projection jobs produce a `projection.images.v1` stage artifact and a
`projection_manifest.json`. The manifest includes generic SfM metadata:
`source_images`, `output_images`, face geometry when applicable, and an
optional `derived_dataset` block. When `output.create_dataset=true`
(default), sfmapi registers the generated image directory as a normal
dataset so later feature, match, and mapping stages can consume it.
If the requested derived dataset name already exists in the project,
sfmapi appends a deterministic task suffix instead of failing the job;
task retries reuse the already-registered derived dataset.

The built-in projection engine is deliberately narrow. With the
`projection` extra installed it handles
`projection.equirectangular_to_cubemap` using vectorized NumPy plus
OpenCV image I/O, with `nearest` and `linear` sampling. `cubic`,
`lanczos`, `projection.cubemap_to_equirectangular`, and
`projection.equirectangular_to_perspective` are contract-only in core;
a backend must advertise those capabilities to serve them.

## SfM stages (single-task jobs)

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/v1/datasets/{did}/features` | `{spec: FeaturesSpec}` | 202 + `JobAccepted` |
| POST | `/v1/datasets/{did}/matches` | `{pairs: PairsSpec, matcher: MatcherSpec, input_artifacts?}` | 202 + `JobAccepted` |
| POST | `/v1/datasets/{did}/verify` | `{spec: VerifySpec, input_artifacts?}` | 202 + `JobAccepted` |

`PairsSpec` and `MatcherSpec` are independent. A deployment can select
pairs with hloc-style retrieval and still match with a COLMAP or learned
matcher. Use optional `provider` only when two installed providers expose
the same portable capability.

Explicit pair lists are portable:

```json
{
  "pairs": {
    "strategy": "explicit",
    "image_pairs": [{"image_name1": "a.jpg", "image_name2": "b.jpg"}]
  },
  "matcher": {"type": "superglue", "provider": "hloc"}
}
```

For large hloc/COLMAP pair files, upload the text file through
`/v1/uploads`, finalize it, then pass
`{"strategy": "explicit", "pairs_blob_sha": "<sha256>"}`. The file
format is one `image1 image2` pair per line.

Use `input_artifacts` to select a previous stage output, for example
`{"features": {"artifact_id": "...", "kind": "features.local.v1"}}`
when matching against a specific feature artifact.

## Pipelines (recipe sugar)

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/v1/projects/{pid}/pipelines/{recipe}` | `{dataset_id, features?, pairs?, matcher?, verify?, spec: PipelineSpec, input_artifacts?}` | 202 + `JobAccepted` |

`recipe ∈ {incremental, global, hierarchical, spherical}` and
`spec.kind` MUST match.

Recipe availability is composed from the selected stage and mapping
capabilities, for example `features.extract.<type>`, the pair/match/verify
capabilities, and `map.incremental`; there is no umbrella recipe
capability.

## Typed dataflow discovery

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/v1/datatypes` | - | `DataTypesContract` |
| GET | `/v1/attributes` | - | `AttributesContract` |
| GET | `/v1/operations` | - | legacy flat `OperationsContract` |
| GET | `/v1/processors` | - | named-port `ProcessorsContract` |
| GET | `/v1/pipelines` | - | `PipelinesContract` |
| POST | `/v1/pipelines:validate` | `{initial_inputs?, steps}` | `PipelineValidateResponse` |
| POST | `/v1/projects/{pid}/pipelines:run` | legacy flat chain or typed DAG request | legacy SfM chain returns 202; native typed DAGs return 501 until the typed executor lands |

The composition law is `A.supplier[out].datatype ==
B.consumer[in].datatype`. `/v1/processors` is the native registry;
`/v1/operations` remains a compatibility projection. Processor
`capabilities` are current execution selectors, not the final P6 split between
capability families and provider/runtime requirements.
The current core `match_graph` contract has one compatibility refinement:
mapping still assumes verified matches, so P5b must split raw and verified
match graphs into nominal DataTypes or make the refinement explicit in
`PortSpec`.

## Radiance / 3DGS

Radiance fields are a capability-gated standard extension for NeRF / 3D
Gaussian Splatting style resources. They are separate from sparse
reconstruction snapshots; dense MVS and mesh generation remain outside the
sfmapi core and belong behind backend actions or downstream APIs.
This extension is alpha until the radiance docs and bench suites are complete.

| Method | Path | Body / Query | Returns |
|---|---|---|---|
| POST | `/v1/projects/{pid}/radiance_fields:train` | `RadianceTrainRequest` | 202 + `JobAccepted` |
| GET | `/v1/projects/{pid}/radiance_fields` | `?page_token=&page_size=` | `Page<RadianceField>` |
| GET | `/v1/radiance_fields/{rfid}` | - | `RadianceField` |
| POST | `/v1/radiance_fields/{rfid}:evaluate` | `RadianceEvaluateRequest` | 202 + `JobAccepted` |
| GET | `/v1/radiance_fields/{rfid}/evaluations` | `?page_token=&page_size=` | `Page<RadianceEvaluation>` |
| GET | `/v1/radiance_evaluations/{eid}` | - | `RadianceEvaluation` |
| GET | `/v1/radiance_evaluations/{eid}/metrics` | - | `RadianceMetrics` |
| GET | `/v1/radiance_evaluations/{eid}/artifacts/metrics.json` | - | JSON |
| GET | `/v1/radiance_fields/{rfid}/snapshots` | - | alpha sequence index: `RadianceSnapshotListResponse` |
| GET | `/v1/radiance_fields/{rfid}/snapshots/{seq}` | - | `RadianceSnapshot` |
| GET | `/v1/radiance_fields/{rfid}/snapshots/{seq}/{name}` | `?download=true` | file bytes |

Portable snapshot file names are `metadata.json`, `summary.json`,
`point_cloud.ply`, `metrics.json`, and `transforms.json` when present. Missing
optional files return 404. Implementations advertise `radiance.train`,
`radiance.evaluate`, or narrower `radiance.metrics.*` capabilities according
to the provider surface they expose.

## Reconstructions / submodels / snapshots

| Method | Path | Query / Body | Returns |
|---|---|---|---|
| GET | `/v1/reconstructions/{rid}` | - | `Reconstruction` |
| GET | `/v1/reconstructions/{rid}/artifacts` | `?page_token=&page_size=&kind=&task_id=&name=` | `Page<StageArtifact>` |
| GET | `/v1/reconstructions/{rid}/submodels` | `?page_token=&page_size=` | `Page<SubModel>` |
| GET | `/v1/submodels/{smid}` | - | `SubModel` |
| GET | `/v1/reconstructions/{rid}/snapshots` | - | `SnapshotListResponse` |
| GET | `/v1/reconstructions/{rid}/snapshots/{seq}/{name}` | `?download=true` | file bytes |
| GET | `/v1/reconstructions/{rid}/snapshots/{seq}/submodels/{idx}/{name}` | `?download=true` | component file bytes |
| GET | `/v1/reconstructions/{rid}/two_view_geometries.json` | - | JSON |
| GET | `/v1/reconstructions/{rid}/correspondence_graph.json` | - | JSON |
| POST | `/v1/reconstructions:merge` | `MergeRequest` | 202 + `JobAccepted` |

Mapping jobs persist one `SubModel` per disconnected component. The
root snapshot file routes expose the largest/default model; use the
submodel snapshot route for a specific component.

Stage artifacts are typed outputs produced by worker stages. They are
the selection surface for ambiguous pipelines, for example when a job
produces both COLMAP SIFT matches and hloc/LightGlue matches, or when
verification emits several candidate pair sets.

## Artifacts

| Method | Path | Query | Returns |
|---|---|---|---|
| GET | `/v1/artifacts/kinds` | - | `Page<ArtifactKind>` |
| GET | `/v1/artifacts/formats` | - | `Page<ArtifactFormat>` |
| POST | `/v1/artifacts:import` | `ArtifactImportRequest` | `StageArtifact` |
| GET | `/v1/artifacts/{artifact_id}` | - | `StageArtifact` |
| GET | `/v1/artifacts/{artifact_id}/content` | `?download=true` | file bytes |
| POST | `/v1/artifacts/{artifact_id}:conversionPlan` | `{provider?, to_format?, accepted_formats?, require_lossless?}` | `ArtifactConversionPlan` |
| POST | `/v1/artifacts/{artifact_id}:convert` | `{provider?, to_format?, accepted_formats?, require_lossless?, to_kind?, name?, options?}` | 202 + `JobAccepted` |
| POST | `/v1/artifacts/{artifact_id}:validate` | - | `ArtifactValidation` |

`StageArtifact.uri` is metadata, not a portability contract. Use
`/content` only for local server-managed regular-file artifacts named
by top-level `StageArtifact.uri` or a backend output descriptor's top-level
`path`. Remote URIs, absent top-level URIs, missing or unmanaged local
paths, `files[]`-only local paths, and local directory artifacts do not
advertise `_links.content` and are not dereferenced by the API.
Directory artifact kinds such as
`reconstruction.snapshot`, `reconstruction.sparse.v1`,
`reconstruction.submodel`, and `radiance.snapshot` publish `uri: null`
when their source URI is a local directory.

Stage submissions can pass selected artifacts through
`input_artifacts`, for example:

```json
{
  "input_artifacts": {
    "verified_matches": {
      "artifact_id": "01HZ...",
      "kind": "matches.verified.v1"
    }
  }
}
```

Core roles validate expected kinds before job creation. Unknown
backend-specific roles are allowed if they use the same dot-key syntax.
Use `/v1/artifacts:import` to register an existing server-local or
remote artifact URI without copying bytes. sfmapi creates a completed
import job/task so the artifact has the same ownership, listing, and
validation behavior as worker-produced artifacts.

Core portable artifact kinds include `features.local.v1`,
`features.global.v1`, `pairs.image_names.v1`, `matches.indexed.v1`,
`matches.coordinates.v1`, `matches.dense.v1`, `matches.verified.v1`,
and `reconstruction.sparse.v1`. Each kind has a default canonical
format id such as `sfmapi.features.local.v1` or
`sfmapi.matches.verified.v1`. Backend-native artifacts should use a
namespaced same-family kind such as `features.hloc_h5` or
`matches.database.colmap`, plus a backend-owned `artifact_format`
such as `hloc.features.h5.v1` or `colmap.matches.database.v1`.

`/v1/artifacts/{artifact_id}:conversionPlan` chooses the shortest
conversion path from the selected backend's artifact contracts. Pass
`provider` to target a specific installed backend provider, or omit it
to use the process default backend. Pass
`accepted_formats` in preference order to let sfmapi negotiate the
target format; pass `to_format` for an exact target. Set
`require_lossless=true` to reject lossy conversion paths. `:convert`
submits the selected conversion as a normal job and requires the
backend to implement `convert_artifact(...)`. Multi-step paths are
executed inside that conversion task by calling the backend once per
step. `:validate` checks the artifact descriptor, core kind/format
compatibility, local managed files, declared byte sizes, SHA-256
digests, and JSON manifests when bytes are available.

`{name}` is one of `cameras.json | images.json | rigs.json |
frames.json | pose_graph.json | summary.json | points.bin |
points_preview.bin | tiles/index.json`.

## Reconstruction-level stages (LRO)

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/v1/reconstructions/{rid}/localize` | `LocalizationRequest{blob_sha, sift?, provider?}` | 202 + `JobAccepted` |
| POST | `/v1/reconstructions/{rid}/georegister` | `GeoregisterRequest{mode, sim3?, provider?}` | 202 + `JobAccepted` |
| POST | `/v1/reconstructions/{rid}:toCubemap` | — | 202 + `JobAccepted` |
| POST | `/v1/reconstructions/{rid}:bundleAdjust` | `BundleAdjustmentSpec` | 202 + `JobAccepted` |
| POST | `/v1/reconstructions/{rid}:triangulate` | `TriangulateSpec` | 202 + `JobAccepted` |
| POST | `/v1/reconstructions/{rid}:poseGraphOptimize` | `PoseGraphSpec` | 202 + `JobAccepted` |
| POST | `/v1/reconstructions/{rid}:export` | `ExportSpec` | 202 + `JobAccepted` |
| POST | `/v1/reconstructions/{rid}:relocalize` | `RelocalizeSpec` | 202 + `JobAccepted` |
| POST | `/v1/reconstructions/{rid}:undistort` | `UndistortSpec` | 202 + `JobAccepted` |

## Dataset-level stages (LRO)

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/v1/datasets/{did}:buildVocabTree` | `VocabTreeSpec` | 202 + `JobAccepted` |
| POST | `/v1/datasets/{did}:configureRig` | `RigConfigSpec` | 202 + `JobAccepted` |
| POST | `/v1/datasets/{did}:estimateTwoView` | `TwoViewSpec` | 202 + `JobAccepted` |

## Similarity

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/v1/datasets/{did}/similarity` | `?image_id=&k=&strategy=&include_self=` | `SimilarityQueryResponse` |
| POST | `/v1/datasets/{did}/similarity:build` | `?strategy=dhash\|vlad&force=` | 200 (`dhash`, optional image-processing extra) or 202 (`vlad`) |

## One-shot (bytes-in / typed-result-out)

For "right now" use cases that don't need a Project/Dataset row.

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/v1/oneshot/features` | image bytes + `?type=&provider=&max_num_features=&use_gpu=&seed=` (`Content-Type: image/...`) | `OneShotFeaturesResponse` |
| POST | `/v1/oneshot/localize` | image bytes + `?recon_id=&type=&provider=&max_num_features=&use_gpu=&seed=` | `OneShotLocalizeResponse` |

Bytes are tempfile'd then deleted; no DB row is created. Capped at
`SCENEAPI_ONESHOT_MAX_REQUEST_BYTES` (50 MiB default).

One-shot feature extraction is gated by the requested
`features.extract.<type>` capability. One-shot localization is gated by
`localize.from_memory`; there is no umbrella one-shot capability.

## Jobs

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/v1/jobs` | `?page_token=&page_size=&status=` | `Page<JobOut>` |
| GET | `/v1/jobs/{jid}/artifacts` | `?page_token=&page_size=&kind=&task_id=&name=` | `Page<StageArtifact>` |
| GET | `/v1/jobs/{jid}` | — | `JobDetail` |
| GET | `/v1/jobs/{jid}/progress` | — | `JobProgressOut` |
| POST | `/v1/jobs/{jid}:cancel` | `?force=true` (optional) | `JobOut` |
| POST | `/v1/jobs/{jid}:resume` | — | 202 + `JobOut` |
| GET | `/v1/jobs/{jid}/events` | `Last-Event-ID` (optional) | SSE stream of `ProgressEvent` |
| GET | `/ws/v1/jobs/{jid}` | WebSocket upgrade | bidirectional events |

`?status=` filters on the closed `JobStatus` set: `pending |
running | succeeded | failed | cancelled | cancelled_dirty`.

### Progress snapshots

`GET /v1/jobs/{jid}/progress` is a compact polling view over durable
task rows plus the latest persisted `ProgressEvent` records. It is
intended for CLIs and dashboards that do not want to hold an SSE
connection open.

```bash
curl -s "$BASE/v1/jobs/$JOB_ID/progress" \
  | jq '{status, progress, current_task_kind, current_phase}'
```

Important fields:

| Field | Meaning |
|---|---|
| `progress` | Best-effort fraction from `0.0` to `1.0`; terminal tasks count as complete |
| `task_counts` | Count of tasks by lifecycle status |
| `current_task_id` / `current_task_kind` | Running task when present, otherwise next pending task |
| `current_phase` | Latest reported phase, such as `matching` or `global_positioning` |
| `latest_event_id` | Durable cursor shared with the SSE stream |
| `tasks[]` | Per-task status, progress, latest event kind, elapsed time, and optional `current` / `total` |

Progress is telemetry. Clients should not use exact percentages for
scheduling decisions; use `status` to decide whether work is terminal.

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
| GET | `/v1/admin/plugins` | `?query=&page_token=&page_size=` | `Page<PluginRegistryItem>` |
| GET | `/v1/admin/plugins/detect-tools` | — | local external-tool detection |
| GET | `/v1/admin/plugins/entry-points` | `?load=false` | installed Python entry points |
| GET | `/v1/admin/plugins/{plugin_id}` | — | plugin manifest + local state |
| POST | `/v1/admin/plugins/{plugin_id}:install` | `{method, github_url?, ref?, package_name?, dry_run?, allow_unsafe_execution?, request_id?, provision_runtime?, force?}` | install plan or result |
| POST | `/v1/admin/plugins/{plugin_id}:enable` | — | plugin state |
| POST | `/v1/admin/plugins/{plugin_id}:disable` | — | plugin state |
| POST | `/v1/admin/plugins/{plugin_id}:doctor` | — | diagnostics |
| POST | `/v1/admin/routing/profiles` | `{name, routes}` | routing state |
| POST | `/v1/admin/routing/default` | `{profile}` | routing state |
| POST | `/v1/admin/routing/provider-priority` | `{providers: [...]}` | routing state |
| POST | `/v1/admin/routing/projects/{project_id}` | `{profile}` | routing state |
| POST | `/v1/admin/routing/workspaces` | `{profile}` | routing state |

Plugin installation is an operator action. `method="uv"` creates a
direct-reference command such as
`uv pip install "scenemap @ git+https://github.com/SceneAPI/SceneMap.git@main"`;
mutable refs produce warnings. `docker` and `external_tool` modes are planned
or recorded as runtime choices. `container_service` records an already-running
plugin service endpoint; when the provider has a configured service URL, the
C++ bridge can replay backend-action jobs through the service execution
endpoint.
When `method="uv"` and `provision_runtime=true`, sfmapi also plans or runs
the installed package's optional `package.provisioning.provision()` hook for
plugin-owned engine downloads, release-asset setup, or native builds. Non-dry
run installs may include `request_id` as a UUID-style idempotency key. The
provisioning result exposes `env_keys`, `redacted_env`, and `outputs`; raw
environment values are not returned.
Backend packages can expose
`[project.entry-points."sceneapi.backends"]`; `sceneapi plugins
entry-points --load` and `sceneapi check-backend --load-entry-points`
validate those contracts.

Dry-run repo-address install example:

```json
{
  "method": "uv",
  "github_url": "https://github.com/SceneAPI/sceneapi_custom.git",
  "ref": "v0.1.0",
  "package_name": "sceneapi-custom",
  "dry_run": true,
  "provision_runtime": true
}
```

Successful install responses include request/provisioning metadata without
secret values:

```json
{
  "plugin_id": "local_test",
  "method": "uv",
  "dry_run": true,
  "installed": false,
  "provisioning_status": "planned",
  "provisioning_error": null,
  "provisioning": {
    "provisioned": false,
    "env_keys": ["SCENEAPI_CUSTOM_HOME"],
    "redacted_env": {"SCENEAPI_CUSTOM_TOKEN": "***"},
    "outputs": {},
    "metadata": {}
  }
}
```

Container-service install example:

```json
{
  "method": "container_service",
  "dry_run": false,
  "allow_unsafe_execution": true,
  "request_id": "550e8400-e29b-41d4-a716-446655440010"
}
```

Container services expose `GET /healthz`, `GET /version`, and the configured
execution path, currently `/execute` by default. Execution requests include a
stable `request_id`, `plugin_id`, `provider`, `action_id`, redacted env/secret
key names, and mounted IO roles (`input`, `output`, `work`, `logs`) with both
host paths and manifest container paths. Successful responses may return
`outputs`, `logs`, and `artifacts`; `/v1/jobs/{job_id}/artifacts` surfaces
those artifacts with stable ids and paths after bridge replay.

Routing selection order is request `provider`, project profile, workspace
profile, default profile, then provider priority. Use provider priority as a
last-resort fallback when several installed providers can satisfy the same
portable stage:

```json
{
  "providers": ["colmap_pycolmap", "colmap_cli"]
}
```

Older SDKs that do not yet expose `provider_priority`, `provisioning_status`,
or `provisioning` should treat those response fields as additive metadata.
They can still call these operator routes through raw HTTP; regenerate the SDK
from the pinned OpenAPI spec before relying on typed accessors for the new
fields.

## ProgressEvent

```{eval-rst}
.. automodule:: sceneapi.server.schemas.progress_event
   :members:
   :no-index:
```

## Pipeline specs

```{eval-rst}
.. automodule:: sceneapi.server.schemas.pipeline_spec
   :members:
   :no-index:
```

## Binary points format

`Content-Type: application/x-sfm-points-v1`. Fixed 32-byte header,
26 bytes per point.

```{eval-rst}
.. automodule:: sceneapi.server.schemas.points_binary
   :members:
   :no-index:
```

Records are written in ascending `point3d_id` order so HTTP `Range`
requests treat the file as a fixed-stride array. `points_preview.bin`
is the same format, decimated.
