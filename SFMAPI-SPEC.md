# SFMAPI Specification

**Version:** `v1.0-draft`
**Status:** Draft. Stable in shape; additive changes only until v2.
**Reference implementation:** [sfmapi/sfmapi](https://github.com/sfmapi/sfmapi)

This document specifies a HTTP / REST + SSE + WebSocket surface for
running Structure-from-Motion (SfM) pipelines as a service. It is
intended to be implementable by any backend; the reference
implementation is one such backend.

The spec is normative when it uses **MUST**, **MUST NOT**, **SHOULD**,
**SHOULD NOT**, and **MAY** as defined by [RFC 2119][rfc2119].

[rfc2119]: https://www.rfc-editor.org/rfc/rfc2119

---

## 1. Goals and non-goals

### 1.1 Goals

- A single REST surface every SfM-aware tool can target, regardless of
  the backend (COLMAP, custom forks, future engines).
- First-class web ergonomics: CORS, ETag, Range, SSE, WebSocket.
- Content-addressed storage so the same dataset never gets re-uploaded
  or re-processed unnecessarily.
- Multi-tenant from the first request: every resource carries a
  `tenant_id` and tenant isolation is server-enforced.
- Job model that supports cancellation, resume, and per-stage caching.
- Decoupled compute: the API surface does not assume a particular
  worker topology.

### 1.2 Non-goals

- This spec does not cover a particular SfM algorithm. It covers the
  *interface* — what a client asks for, what a server returns. The
  backend may use COLMAP, OpenSfM, custom code, etc.
- This spec does not cover infrastructure (deployment topology,
  GPU scheduling, Helm charts).
- This spec does not cover offline / batch SDK ergonomics — those are
  client-side and may be implemented per-language.
- This spec does NOT cover **dense multi-view stereo (MVS) or mesh
  generation**. Those belong to a separate, downstream API (see
  Appendix D). Sparse SfM and dense MVS have different memory
  shapes, different consumers, and different lifecycles.
- This spec does NOT cover **image segmentation / mask generation**.
  Masks are an *input* to SfM (consumed by the feature extractor); a
  segmentation pipeline that produces them is out of scope.

### 1.3 Conformance levels

Every endpoint in this spec belongs to exactly one conformance level:

- **Core** — A conformant `sfmapi` server **MUST** implement the
  endpoint. The wire shapes here are the standard.
- **Standard extension (capability-flagged)** — Optional, but
  standardized. A server **MUST** advertise the corresponding
  `<capability>` in `GET /v1/capabilities` if and only if it
  implements the endpoint. If unavailable, the endpoint **MUST**
  return `501 Not Implemented` with
  `application/problem+json` carrying `capability: <name>`.
- **Standard extension (capability-composed)** -- Optional, but
  standardized. Availability is derived from the underlying portable
  capabilities named by the endpoint, such as `map.incremental`,
  `features.extract.sift`, or `localize.from_memory`, rather than a
  single umbrella capability.
- **Preview** — Shipped and always served by the reference
  implementation, but not yet stable and not part of the default
  contract: the reference server omits these operations from its
  OpenAPI document (and therefore from generated SDKs and the pinned
  `openapi.json`) unless the deployment opts in via
  `SFMAPI_EXPOSE_PREVIEW_APIS=true`. When exposed, each preview
  operation carries `x-sfmapi-conformance: preview`. Wire shapes here
  **MAY** change without a major version bump; other implementations
  **MAY** omit these endpoints entirely, and compliance test suites
  **MUST NOT** require them.
- **Reference-implementation-only** — Ships in the reference
  `sfmapi/sfmapi` repository for operator convenience but is **not**
  part of the standard. Other backends **MAY** omit these endpoints
  entirely. Examples: `/v1/admin/api-keys`, `/metrics`. Compliance
  test suites **MUST NOT** require them.

Each subsection in Section 6 is tagged. `[Core]` marks required core surfaces.
`[Standard extension: <capability>]` marks a single capability-flagged
surface, `[Standard extension: capability-composed]` marks a surface
gated by the capabilities named in that subsection, `[Preview]` marks
preview surfaces fenced out of the default OpenAPI document, and
`[Reference-only]` items are explicitly marked.

---

## 2. Versioning and evolution

- The current version is **`v1`**, served under the `/v1/` URL prefix.
- A `v1` server **MUST** accept `/v1/` requests.
- A server **MAY** add new endpoints, new fields on existing
  responses, new optional request fields inside documented extension
  envelopes or capability-gated revisions, and new enum values without
  bumping the major version, as long as well-behaved older clients
  continue to function.
- A server **MUST NOT** remove existing fields, repurpose enum values,
  change a 2xx response shape, or change the meaning of an HTTP
  method on an existing path within `v1`.
- Request body schemas for new capability-gated, plugin, typed-dataflow,
  backend-action, and artifact-conversion surfaces are strict by default:
  a server **MUST** reject unknown request fields with 422 unless the field
  is inside a documented extension envelope such as `backend_options`,
  `input_artifacts`, `special_inputs`, `special_attributes`, or a vendor
  `x-` field accepted by that endpoint. Legacy stable resource bodies in
  `v1` **MAY** ignore unknown fields for compatibility with deployed clients.
- Request `x-` fields are valid only where an endpoint documents that
  extension namespace. A server **MAY** add `x-`-prefixed fields to
  responses and documented extension envelopes; clients **MUST** ignore
  unrecognised response `x-` fields.
- The shape of `_links` (§3.5) is part of the spec; the *contents* are
  advisory and may grow.

### 2.1 Backends and capabilities

sfmapi is **a wire standard, not a single implementation**. Different
deployments can be powered by different SfM engines (COLMAP,
COLMAP-mod, OpenMVG, Theia, hloc, custom code) and **MUST NOT** be
required to support every endpoint in this spec.

- A small set of capabilities is **CORE** (project / dataset / image
  CRUD, uploads, jobs, events). Every conforming server **MUST**
  expose these routes; they are not capability-gated, but normal
  validation, authentication, quota, not-found, storage, and server
  errors still apply.
- The remaining capabilities are **OPTIONAL** feature flags. A server
  advertises which OPTIONAL flags it supports via
  `GET /v1/capabilities` (see §3.11). Endpoints whose capability is
  not advertised **MUST** return `501 Not Implemented` with a
  problem+json body whose `capability` extra carries the canonical
  feature name.
- Clients **SHOULD** call `/v1/capabilities` once at startup and gate
  UI affordances on the response. Clients **MUST** treat the absence
  of an OPTIONAL key as `false`.

This is what lets sfmapi be a high-level standard rather than a
COLMAP wrapper: the schemas and HTTP shapes don't change between
backends; only the set of advertised capabilities does.

The reference implementation isolates the backend behind a single
Python protocol (`app.adapters.backend.SfmBackend`). sfmapi itself
ships **no concrete backend** — engines like pycolmap, OpenSfM, hloc,
or custom forks live in their own packages, implement
`SfmBackend`, and register at app startup with
`register_backend("name", MyBackend)`. Adding a new backend is purely
additive: no schema, endpoint, or worker-task signature changes.

Reference implementations MAY also expose backend-native action
catalogs. These are not portable capability flags: action ids such as
`colmap.feature_extractor` describe tool-specific extensions and MUST
NOT be advertised as canonical sfmapi capabilities.

---

## 3. Conventions

### 3.1 IDs

Resource IDs **MUST** be opaque strings safe for URL path
inclusion. The reference implementation uses 26-char ULIDs; clients
**MUST NOT** parse the ID format. IDs **MUST** be unique within a
tenant.

### 3.2 Timestamps

All timestamps **MUST** be ISO-8601 / RFC 3339 strings in UTC, e.g.
`"2026-05-02T18:42:01.123Z"`.

### 3.3 Hashes

Content addresses **MUST** be lower-case hex SHA-256 digests
(64 chars).

### 3.4 Errors

A non-2xx response **MUST** be a `application/problem+json`
[RFC 7807][rfc7807] document with at minimum:

```json
{
  "type": "https://sfmapi.github.io/errors/<slug>",
  "title": "Human-readable category",
  "status": 409,
  "detail": "Optional, free-form description",
  "instance": "/v1/projects/abc"
}
```

The HTTP status → error-class mapping the spec defines:

| HTTP | Error class                      |
|------|----------------------------------|
| 400  | `bad_request`                    |
| 403  | `tenant_violation`, `auth`       |
| 404  | `not_found`                      |
| 409  | `conflict`                       |
| 413  | `quota_exceeded` (storage)       |
| 422  | `validation`                     |
| 429  | `quota_exceeded` (rate / GPU-s)  |
| 501  | `pycolmap_unavailable` / `capability_unavailable` |
| 507  | `storage`                        |

Other 4xx/5xx codes **MAY** be used; clients **SHOULD** treat any
non-2xx as an error.

[rfc7807]: https://www.rfc-editor.org/rfc/rfc7807

### 3.5 HAL-lite `_links`

Every resource representation **SHOULD** include a `_links` block
containing at minimum a `self` link, plus zero or more named links
to subresources. Each link is `{"href": "<absolute or root-relative URL>"}`.

```json
{
  "project_id": "...",
  "_links": {
    "self":     { "href": "/v1/projects/abc" },
    "datasets": { "href": "/v1/projects/abc/datasets" }
  }
}
```

Clients **SHOULD** prefer `_links` over hard-coded URL templates when
navigating between resources.

### 3.6 Pagination

List endpoints **MUST** follow [AIP-158][aip158] and return:

```json
{
  "items":           [...],
  "next_page_token": "<opaque string>" | null,
  "total":           <int> | null
}
```

Documented snapshot/navigation-index endpoints, such as reconstruction snapshot
indexes and the alpha radiance snapshot sequence index, **MAY** use a smaller
custom envelope. They must be called out explicitly in their resource section
instead of being implied to be a general list endpoint.

Clients pass `?page_token=` and `?page_size=` to continue. `total`
**MAY** be `null` when counting is expensive. Clients **MUST NOT**
parse the page token.

[aip158]: https://google.aip.dev/158

### 3.7 Idempotency

`POST` endpoints that create a resource **SHOULD** accept an
`Idempotency-Key` request header. If the same key + same tenant is
seen again, the server **MUST** return the original resource (or
upload state) instead of creating a duplicate.

### 3.8 Caching

For immutable resources (sealed snapshots, content-addressed blobs,
finalized uploads), the server **MUST** emit a strong `ETag` and
**MUST** honor `If-None-Match` with a `304 Not Modified` response.

For long-cacheable resources the server **SHOULD** emit
`Cache-Control: public, max-age=<n>, immutable`.

Range requests (`Range: bytes=A-B`) **MUST** be honored on byte
endpoints. The binary points format (§7.1) is fixed-stride for
exactly this reason.

### 3.9 Long-running operations (LROs)

Any endpoint that submits work **MUST** return:

```
HTTP/1.1 202 Accepted
Location: /v1/jobs/<job_id>
Content-Type: application/json

{
  "job_id":   "<id>",
  "task_ids": ["<id>", ...],
  "recon_id": "<id>" | null
}
```

The created `Job` resource is then observable via §6.7.

### 3.11 Capability discovery

```http
GET /v1/capabilities
200 OK
content-type: application/json

{
  "backend": {
    "name":    "colmap_mod",
    "version": "3.13.0.dev",
    "vendor":  "ETH3D / sfmapi"
  },
  "features": {
    "projects.crud":             true,
    "datasets.crud":             true,
    "images.crud":               true,
    "uploads.chunked":           true,
    "jobs.read":                 true,
    "events.sse":                true,
    "spec.read":                 true,

    "features.extract.sift":     true,
    "pairs.exhaustive":          true,
    "pairs.sequential":          true,
    "pairs.spatial":             true,
    "pairs.vocabtree":           true,
    "pairs.explicit":            true,
    "matchers.nn-mutual":        true,
    "matches.verify":            true,

    "map.incremental":           true,
    "map.global":                true,
    "map.hierarchical":          true,
    "map.spherical":             true,

    "ba.standard":               true,
    "ba.two_stage":              true,
    "ba.rig":                    false,
    "triangulate.retri":         true,
    "relocalize.images":         true,
    "pgo.optimize":              true,
    "geometry.two_view":         false,

    "export.ply":                true,
    "export.nvm":                true,
    "export.colmap_text":        true,
    "export.colmap_bin":         true,

    "similarity.dhash":          true,
    "similarity.vlad":           true,
    "index.vocab_tree":          false,

    "localize.from_memory":      true,
    "georegister.sim3":          true,
    "georegister.gps":           false,
    "image.undistort":           false,
    "projection.equirectangular_to_cubemap": true,
    "projection.cubemap_to_equirectangular": false,
    "projection.equirectangular_to_perspective": false,
    "projection.cubemap_rig":    true,
    "rigs.configure":            false,

    "pose_priors.read_write":    true,
    "pose_priors.mapping":       false,
    "compute.in_memory":         false,
    "segment.sam":               false
  }
}
```

> Dense MVS and meshing are **out of scope** for sfmapi (Appendix D);
> capability names like `dense.*` / `mesh.*` are intentionally absent
> from the canonical vocabulary. A backend MAY still expose those
> operations through the backend-action catalog (§6.10).

`backend` identifies the SfM engine powering this deployment;
`features` is a flat dict from canonical capability name to bool.
The CORE feature names are listed in §6.1; OPTIONAL feature names
are owned by the spec. The public capability surface is closed until
plugin-qualified capability ids are versioned and client-gated; unknown
backend-local names are not advertised in `features`.

When a request hits an OPTIONAL feature whose flag is `false`, the
server **MUST** respond:

```http
501 Not Implemented
content-type: application/problem+json

{
  "type":       "https://sfmapi.github.io/errors/capability_unavailable",
  "title":      "Capability not available in this deployment",
  "status":     501,
  "detail":     "capability 'map.hierarchical' not supported by the current backend",
  "capability": "map.hierarchical"
}
```

### 3.10 CORS

Servers **MUST** support CORS preflight (`OPTIONS *`) and **SHOULD**
expose the following response headers to browsers:
`ETag, Last-Modified, Content-Range, Location, Link`.

---

## 4. Resource model

The spec defines nine first-class nouns:

```text
Tenant
  └── Project              (group of datasets)
        └── Dataset        (set of images + camera/rig metadata)
              ├── ImageSource   (where bytes live; immutable)
              ├── Image*        (one per registered image)
              └── Reconstruction*
                    └── SubModel*    (one per produced sparse/{idx})

Job                        (user-facing intent)
  └── Task*                (DAG node; one per stage)

Snapshot                   (sealed, immutable read view of a SubModel)
```

### 4.1 Project

```json
{
  "project_id":   "01HZ...",
  "tenant_id":    "default",
  "name":         "vacation-2026",
  "description":  null,
  "created_at":   "2026-05-02T...",
  "_links": { "self": {...}, "datasets": {...}, "pipelines": {...} }
}
```

### 4.2 ImageSource

A logical reference to where the bytes live. **Immutable.** To change
where a dataset's bytes come from, create a new dataset.

```json
{ "kind": "upload" }
{ "kind": "local",  "root": "/data/photos", "recursive": true }
{ "kind": "s3",     "bucket": "my-bucket", "prefix": "scenes/a/" }
```

### 4.3 Dataset

```json
{
  "dataset_id":               "01HZ...",
  "tenant_id":                "default",
  "project_id":               "01HZ...",
  "source_id":                "01HZ...",
  "name":                     "trip",
  "camera_model":             "SIMPLE_RADIAL",
  "intrinsics_mode":          "single_camera" | "per_image" | "per_folder",
  "is_spherical":             false,
  "respect_exif_orientation": false,
  "rig_config_json":          { ... } | null,
  "active_maskset_id":        null,
  "manifest_hash":            "<sha256 of sorted (name, content_sha)>",
  "created_at":               "2026-05-02T...",
  "_links": { "self": {...}, "images": {...}, "features": {...}, ... }
}
```

`manifest_hash` is the canonical content address of the dataset's
*image set*. Two datasets with the same manifest_hash are
interchangeable as inputs to any subsequent stage.

### 4.4 Image

```json
{
  "image_id":    "01HZ...",
  "dataset_id":  "01HZ...",
  "name":        "img_001.jpg",
  "content_sha": "<sha256 or 0x00 placeholder for local sources>",
  "source_kind": "upload" | "local" | "s3",
  "rel_path":    "subdir/img_001.jpg" | null,
  "byte_size":   123456 | null,
  "width":       4032 | null,
  "height":      3024 | null,
  "created_at":  "...",
  "_links": { "self": {...}, "bytes": {...}, "thumbnail": {...}, "exif": {...} }
}
```

### 4.5 Reconstruction

A run of a mapping pipeline. Identified by the cache key
`(dataset_snapshot_hash, params_hash, runtime_version_id)`.

```json
{
  "recon_id":               "01HZ...",
  "project_id":             "01HZ...",
  "dataset_id":             "01HZ...",
  "dataset_snapshot_hash":  "<sha256>",
  "spec":                   { ...PipelineSpec },
  "rv_id":                  "<runtime_version_id>",
  "status":                 "pending" | "running" | "succeeded" | "failed",
  "created_at":             "...",
  "_links": { "self": {...}, "submodels": {...}, "snapshots": {...} }
}
```

### 4.6 SubModel

One per produced `sparse/{idx}` directory. A reconstruction may
contain N sub-models. Iterative refinement (BA round, retriangulation,
reloc) produces a **revision** of a SubModel via `parent_submodel_id`.

```json
{
  "submodel_id":         "01HZ...",
  "recon_id":            "01HZ...",
  "idx":                 0,
  "parent_submodel_id":  null,
  "summary":             { "num_reg_images": 12, "num_points3D": 4567, ... },
  "rigidity":            { "sigma_0": ..., "sigma_1": ..., ... } | null,
  "snapshot_seq":        7,
  "sealed_path":         "<server-side path; informational>",
  "created_at":          "...",
  "_links": { "self": {...}, "reconstruction": {...} }
}
```

### 4.7 Job and Task

```json
// Job
{
  "job_id":           "01HZ...",
  "tenant_id":        "default",
  "project_id":       "01HZ...",
  "recipe":           "incremental" | "global" | ... | "features" | "matches" | ...,
  "status":           "pending" | "running" | "succeeded" | "failed" |
                      "cancelled" | "cancelled_dirty",
  "cancel_requested": false,
  "cancel_force":     false,
  "created_at":       "...",
  "started_at":       "..." | null,
  "finished_at":      "..." | null,
  "error_class":      "OOMError" | "CudaContextError" | ... | null,
  "error_message":    "..." | null,
  "_links":           { "self": {...}, "events": {...}, "ws": {...} }
}

// Task (one per DAG node)
{
  "task_id":      "01HZ...",
  "job_id":       "01HZ...",
  "kind":         "extract" | "match" | "verify" | "map" | "ba" |
                  "triangulate" | "relocalize" | "pgo" | "export" | "segment" | "vlad",
  "status":       "<as Job.status>",
  "cache_key":    "<sha256>",
  "inputs_hash":  "<sha256>",
  "params_hash":  "<sha256>",
  "outputs_ref":  { ... } | null
}
```

The `JobDetail` shape is `Job & { tasks: Task[] }`.

### 4.8 Upload

```json
{
  "upload_id":      "01HZ...",
  "state":          "open" | "received" | "finalized",
  "expected_size":  102400,
  "received_bytes": 102400,
  "blob_sha":       "<sha256>" | null,
  "expires_at":     "..."
}
```

### 4.9 Snapshot

A snapshot is **not** a database row; it is a directory of immutable
files keyed by `(reconstruction, seq)`. The server enumerates sealed
seqs and serves files within them. Required filenames are listed in
§7.

---

## 5. Auth

The spec defines two auth modes; servers **MUST** support at least
one.

### 5.1 `none` mode

Every request resolves to a single `default` tenant. Suitable for
local development. Servers in this mode **SHOULD** emit a warning
header `X-SFMAPI-Auth: none` so clients can detect it.

### 5.2 `api_key` mode

Clients pass `Authorization: Bearer <opaque-key>`. The server resolves
the key to a tenant and uses it for every subsequent operation.

Issuing keys is out of scope for this spec; the reference
implementation exposes `POST /v1/admin/api-keys`. Servers **MAY**
expose a different admin path or none at all (manual provisioning).

---

## 6. The endpoint surface

All paths are `v1`-prefixed. Square brackets indicate optional path
segments. **Bold** = required. Italics = optional.

### 6.1 Health and meta

| Method | Path                | Purpose                                 |
|--------|---------------------|-----------------------------------------|
| GET    | `/healthz`          | Liveness — always 200 if process alive  |
| GET    | `/readyz`           | Readiness — DB/queue reachable, ...     |
| GET    | `/version`          | Versions of server + engine             |
| GET    | `/spec`             | Discovery — spec id/version + doc/OpenAPI pointers |
| GET    | `/openapi.json`     | OpenAPI 3.1 document                    |
| GET    | `/metrics`          | Prometheus exposition (optional)        |
| GET    | `/v1/capabilities`  | Capability discovery (see §3.11)        |
| GET    | `/v1/camera-models` | `Page<CameraModel>` — supported camera models |

### 6.2 Projects

| Method  | Path                        | Body / Returns                          |
|---------|-----------------------------|-----------------------------------------|
| POST    | `/v1/projects`              | `{name, description?}` → `Project` (201)|
| GET     | `/v1/projects`              | `Page<Project>`                         |
| GET     | `/v1/projects/{pid}`        | `Project`                               |
| PATCH   | `/v1/projects/{pid}`        | `{name?, description?}` + optional `?update_mask=` → `Project` |
| DELETE  | `/v1/projects/{pid}`        | 204                                     |

### 6.3 Uploads (chunked)

| Method  | Path                                     | Body / Headers                                     | Returns        |
|---------|------------------------------------------|----------------------------------------------------|----------------|
| POST    | `/v1/uploads`                            | `{expected_size, content_type?, expected_sha?}` + `Idempotency-Key` | `Upload` (201) |
| GET     | `/v1/uploads/{uid}`                      | —                                                  | `Upload`       |
| PATCH   | `/v1/uploads/{uid}`                      | raw chunk + `Content-Range: bytes A-B/T`           | `Upload`       |
| POST    | `/v1/uploads/{uid}:finalize`             | `{}` or `X-Content-SHA256` header                  | `Upload`       |

After `finalize`, the bytes live at `blob_sha` and can be referenced
from `Image.blob_sha` (§6.5).

### 6.4 Datasets

| Method  | Path                                          | Returns         |
|---------|-----------------------------------------------|-----------------|
| POST    | `/v1/projects/{pid}/datasets`                 | `Dataset` (201) |
| GET     | `/v1/projects/{pid}/datasets`                 | `Page<Dataset>` |
| GET     | `/v1/projects/{pid}/datasets/{did}`           | `Dataset`       |
| PATCH   | `/v1/projects/{pid}/datasets/{did}`           | `Dataset` + optional `?update_mask=` |

PATCH endpoints follow AIP-161 when `update_mask` is provided:
comma-separated field paths are relative to the request body and
each named path **MUST** also appear in the body. When `update_mask`
is omitted, servers apply the legacy implicit mask: fields present in
the JSON body are updated and absent fields are left unchanged.

`POST` body:

```json
{
  "name":                     "trip",
  "source":                   { "kind": "upload" | "local" | "s3", ... },
  "camera_model":             "SIMPLE_RADIAL",
  "intrinsics_mode":          "single_camera",
  "is_spherical":             false,
  "rig_config":               null,
  "respect_exif_orientation": false
}
```

### 6.5 Images

| Method  | Path                                       | Returns       |
|---------|--------------------------------------------|---------------|
| POST    | `/v1/datasets/{did}/images`                | `Image` (201) |
| POST    | `/v1/datasets/{did}/images:batchCreate`    | `BatchCreateImagesResponse` (201) |
| GET     | `/v1/datasets/{did}/images`                | `Page<Image>` |
| GET     | `/v1/images/{image_id}`                    | `Image`       |
| DELETE  | `/v1/images/{image_id}`                    | 204           |
| DELETE  | `/v1/datasets/{did}/images/{name}`         | 204 (legacy label-addressed delete) |
| GET     | `/v1/images/{image_id}/bytes`              | image bytes (Range, ETag) |
| GET     | `/v1/images/{image_id}/thumbnail?size=N`   | JPEG (Cache-Control) |
| GET     | `/v1/images/{image_id}/exif`               | JSON          |

`POST .../images:batchCreate` body (AIP-231):

```json
{ "requests": [ { "name": "...", "blob_sha": "..." }, ... ] }
```

Returns `BatchCreateImagesResponse`:

```json
{ "images": [ { "image_id": "...", "name": "...", ... } ] }
```

Servers **MUST** cap batches at 1000 items.

### 6.6 SfM stages

Stage endpoints take only `{spec}`. Image source and database path
are derived server-side from the dataset's `source` and the cached
reconstruction.

| Method | Path                                | Body                  | Returns |
|--------|-------------------------------------|-----------------------|---------|
| POST   | `/v1/datasets/{did}/features`       | `{spec: FeaturesSpec}`| 202 + LRO |
| POST   | `/v1/datasets/{did}/matches`        | `{pairs: PairsSpec, matcher: MatcherSpec}` | 202 + LRO |
| POST   | `/v1/datasets/{did}/verify`         | `{spec: VerifySpec}`  | 202 + LRO |

A dataset with no registered images **MUST** be rejected with 422
*before* a job is created.

Every stage spec carries an optional `provider` selector. When set,
the server **MUST** route execution to the backend factory registered
for that sfm_hub provider id; when unset, the server **MAY** resolve
one through routing profiles, and **MUST** raise `ProviderAmbiguityError`
(422 with `candidates`) if several enabled providers can satisfy the
stage with no resolution rule. Utility stages (`merge_recons`,
`georegister`, `:toCubemap`, `localize`, `:similarity:build`,
`:renderCubemap`, `:projectImages`, `:renderEquirectangular`,
`:renderPerspective`) accept the same selector through a request
body field or query parameter.

### 6.7 Jobs and progress

| Method  | Path                          | Body / Headers                       | Returns             |
|---------|-------------------------------|--------------------------------------|---------------------|
| GET     | `/v1/jobs`                    | `?project_id=&status=&page_token=&page_size=` | `Page<Job>` |
| GET     | `/v1/jobs/{jid}`              | —                                    | `JobDetail`         |
| POST    | `/v1/jobs/{jid}:cancel`       | `?force=true`                        | `Job` (cancel set)  |
| POST    | `/v1/jobs/{jid}:resume`       | —                                    | 202 + `Job`         |
| GET     | `/v1/jobs/{jid}/progress`     | —                                    | `JobProgress` (compact polling snapshot) |
| GET     | `/v1/jobs/{jid}/events`       | `Last-Event-ID: <int>`               | SSE (`ProgressEvent`) |
| GET     | `/v1/jobs/{jid}/artifacts`    | `?kind=`                             | `Page<StageArtifact>` |
| GET     | `/ws/v1/jobs/{jid}`           | WebSocket upgrade                    | (see §8)            |

`GET /v1/jobs/{jid}/progress` complements `/events` for dashboards and
CLIs that prefer polling over holding an SSE connection open.

### 6.8 Pipelines (recipe sugar) [Standard extension: capability-composed]

| Method | Path                                                | Body                                                | Returns |
|--------|-----------------------------------------------------|-----------------------------------------------------|---------|
| POST   | `/v1/projects/{pid}/pipelines/{recipe}` *(deprecated)* | `{dataset_id, spec, features?, pairs?, matcher?, verify?}` | 202 + LRO |

`recipe ∈ {incremental, global, hierarchical, spherical, feed_forward}` and
`spec.kind` **MUST** match `recipe` or the request **MUST** be rejected
with 422. The `feed_forward` recipe composes the one-stage
`image_set → map` DAG (no feature/pair/match/verify stages; the
`features` / `pairs` / `matcher` / `verify` request sections are
ignored) and is gated by the `map.feed_forward` capability.

> **Deprecation note (custom-verb normalization, pre-1.0).** The
> `/{recipe}` path-segment form is deprecated in favour of the AIP-136
> custom verb `POST /v1/projects/{pid}/pipelines:run` (§6.8.2), whose
> legacy flat-chain grammar composes the same
> `features → matches → verify → map` DAG. Servers **MUST** still
> serve `/{recipe}` for now (it is marked `deprecated: true` in the
> OpenAPI document); it will be removed no earlier than the next
> pre-1.0 breaking window.

Recipe availability is composed from the selected stages and mapping
kind. For example, an incremental recipe requires the provider-selected
feature, pair, match, verify, and `map.incremental` capabilities; there
is no separate recipe capability.

### 6.8.1 One-shot endpoints [Standard extension: capability-composed]

For "right now" use cases that don't need a Project / Dataset / Job
row. Bytes are tempfile'd and deleted; no persistent state is
created. Capped at `SFMAPI_ONESHOT_MAX_REQUEST_BYTES` (50 MiB
default).

| Method | Path                  | Body / Query                                      | Returns                  |
|--------|-----------------------|---------------------------------------------------|--------------------------|
| POST   | `/v1/oneshot/features`  | image bytes + `?type=&provider=&max_num_features=&use_gpu=&seed=` | `OneShotFeaturesResponse` |
| POST   | `/v1/oneshot/localize`  | image bytes + `?recon_id=&type=&provider=&...`    | `OneShotLocalizeResponse` |

The optional `provider` selector routes execution to a specific
backend installed through sfm_hub without changing the process-wide
`SFMAPI_BACKEND`. Unknown providers fail with 422.

One-shot feature extraction is gated by the requested
`features.extract.<type>` capability. One-shot localization is gated by
`localize.from_memory`. There is no umbrella `oneshot` capability.

### 6.8.2 Typed dataflow discovery [Preview]

| Method | Path                                      | Returns                    |
|--------|-------------------------------------------|----------------------------|
| GET    | `/v1/datatypes`                           | `DataTypesContract`        |
| GET    | `/v1/attributes`                          | `AttributesContract`       |
| GET    | `/v1/operations`                          | legacy `OperationsContract`|
| GET    | `/v1/processors`                          | `ProcessorsContract`       |
| GET    | `/v1/pipelines`                           | `PipelinesContract`        |
| POST   | `/v1/pipelines:validate`                  | `PipelineValidateResponse` |
| POST   | `/v1/projects/{pid}/pipelines:run`        | Core route: legacy flat SfM chains return 202; native typed Processor DAGs preflight then return 501 until typed execution |

The native composition law is:

```
A.supplier[out].datatype == B.consumer[in].datatype
```

The current `match_graph` core contract has one compatibility refinement:
mapping still assumes verified matches. That hidden state is not part of the
final typed-execution law; the P5b release must split raw and verified match
graphs into nominal DataTypes or make the refinement explicit in `PortSpec`.

`POST /v1/pipelines:validate` and
`POST /v1/projects/{pid}/pipelines:run` accept two request grammars:

- Legacy Operation grammar: each step is either an operation id string or
  `{op, params?, provider?}`. This grammar is the compatibility projection of
  `/v1/operations`; plugin processors **MUST NOT** rely on it unless they are
  also projected as Operations by that implementation. The core executable SfM
  chain is exactly `features -> pairs -> matches -> verify -> map`; its
  `params` are validated by the corresponding stage schemas, including legacy
  aliases such as SIFT option aliases. Provider selectors are allowed for that
  executable chain and are routed by the recipe executor. Provider selectors on
  other legacy custom chains remain validation errors.
- Native Processor grammar: each step is `{ref?, processor, attributes?,
  params?, provider?, wires?}`. `ref` defaults to `step_<index>` and the value
  `inputs` is reserved for synthetic external suppliers. `params` is a legacy
  alias for `attributes`; when both are present, `attributes` wins on key
  overlap. `wires` maps named consumer ports to `producer_ref.supplier_port`
  strings, or to ordered arrays for `multiple=true` ports. A syntactically and
  type-valid native request may include provider selectors as preflight
  metadata, but servers without `pipelines.custom_execution` **MUST** return
  501 from `:run` instead of creating non-drainable jobs.

The current request model exposes `initial_inputs: string[]` as a compatibility
adapter for synthetic `inputs.<datatype>` suppliers. The durable Pipeline
library shape is reference-keyed external inputs, so a future request can
distinguish two external suppliers with the same Data Type. Implementations
that advertise custom typed execution **MUST** support the reference-keyed form
or document an equivalent non-ambiguous external-input grammar.

Validation failures from `:validate` return 200 with
`PipelineValidateResponse.valid=false`; failures from `:run` return the same
reason/path discipline inside the 422 problem response. Common reasons include
`unknown_processor`, `unknown_port`, `unknown_attribute`,
`missing_required_port`, `missing_required_attribute`, `ambiguous_input`,
`datatype_mismatch`, `invalid_attribute`, `invalid_fan_in`,
`duplicate_step_ref`, `duplicate_initial_input`, `unknown_datatype`,
`unverified_match_graph`, and `provider_unsupported`. These names match the generated
`PipelinesContract.validation_reasons` contract.

`/v1/processors` is the named-port registry for `consumer`, `supplier`, and
typed `attributes`; `/v1/operations` remains the flat compatibility
projection. Processor `capabilities` in this contract are current execution
selectors used by the bridge-era scheduler. The P6 contract split will expose
capability-family metadata separately from provider/runtime requirements.

`/v1/datatypes`, `/v1/attributes`, `/v1/operations`, `/v1/processors`,
`/v1/pipelines`, and `/v1/pipelines:validate` are Preview discovery and
validation endpoints (§1.3): the reference server always serves them but
omits them from the default OpenAPI document unless
`SFMAPI_EXPOSE_PREVIEW_APIS=true`. `POST /v1/projects/{pid}/pipelines:run`
remains **Core** and preserves the
legacy v1 flat SfM operation chain (`features -> pairs -> matches -> verify ->
map`) as an executable 202 job-submission shape. Actual custom typed Processor
DAG execution is optional and gated by `pipelines.custom_execution`; servers
without that capability **MUST** return 501 after a native typed Processor DAG
request is syntactically and type-valid.

### 6.8.3 Radiance fields / 3D Gaussian Splatting [Standard extension: capability-composed]

Radiance resources model plugin-owned NeRF / 3DGS style outputs without
promoting dense MVS or mesh generation into the sfmapi core. Implementations
that expose training or evaluation **MUST** advertise the matching portable
capability (`radiance.train`, `radiance.evaluate`, or the narrower
`radiance.metrics.*` capability when applicable).
This extension is alpha until the radiance API guide, plugin authoring guide,
deployment guide, migration notes, and bench suites are complete.

| Method | Path                                                            | Body / Query            | Returns |
|--------|-----------------------------------------------------------------|-------------------------|---------|
| POST   | `/v1/projects/{pid}/radiance_fields:train`                      | `RadianceTrainRequest`  | 202 + `JobAccepted` |
| GET    | `/v1/projects/{pid}/radiance_fields`                            | `?page_token=&page_size=` | `Page<RadianceField>` |
| GET    | `/v1/radiance_fields/{rfid}`                                    | -                       | `RadianceField` |
| POST   | `/v1/radiance_fields/{rfid}:evaluate`                           | `RadianceEvaluateRequest` | 202 + `JobAccepted` |
| GET    | `/v1/radiance_fields/{rfid}/evaluations`                        | `?page_token=&page_size=` | `Page<RadianceEvaluation>` |
| GET    | `/v1/radiance_evaluations/{eid}`                                | -                       | `RadianceEvaluation` |
| GET    | `/v1/radiance_evaluations/{eid}/metrics`                        | -                       | `RadianceMetrics` |
| GET    | `/v1/radiance_evaluations/{eid}/artifacts/metrics.json`         | -                       | JSON |
| GET    | `/v1/radiance_fields/{rfid}/snapshots`                          | -                       | alpha sequence index: `RadianceSnapshotListResponse` |
| GET    | `/v1/radiance_fields/{rfid}/snapshots/{seq}`                    | -                       | `RadianceSnapshot` |
| GET    | `/v1/radiance_fields/{rfid}/snapshots/{seq}/{name}`             | `?download=true`        | file bytes |

Portable snapshot file names are `metadata.json`, `summary.json`,
`point_cloud.ply`, `metrics.json`, and `transforms.json` when present. A
missing optional file **MUST** return 404.

### 6.9 Reconstructions, submodels, snapshots

| Method | Path                                                     | Returns                       |
|--------|----------------------------------------------------------|-------------------------------|
| GET    | `/v1/reconstructions/{rid}`                              | `Reconstruction`              |
| GET    | `/v1/reconstructions/{rid}/submodels`                    | `Page<SubModel>`              |
| GET    | `/v1/submodels/{smid}`                                   | `SubModel`                    |
| GET    | `/v1/reconstructions/{rid}/artifacts`                    | `Page<StageArtifact>` (`?kind=`) |
| GET    | `/v1/reconstructions/{rid}/snapshots`                    | `{seqs: int[], _links: {...}}`|
| GET    | `/v1/reconstructions/{rid}/snapshots/{seq}/{name}`       | file bytes (ETag, immutable)  |
| GET    | `/v1/reconstructions/{rid}/snapshots/{seq}/submodels/{idx}/{name}` | per-submodel file bytes (ETag, immutable) |

Where `{name}` is one of `cameras.json | images.json | rigs.json |
frames.json | points.bin | points_preview.bin | summary.json`. The
top-level `.../{seq}/{name}` route serves the primary submodel
(`sparse/0`); the `.../{seq}/submodels/{idx}/{name}` route addresses
any submodel `idx` of a multi-model reconstruction.

#### 6.9.1 Octree tiles (optional)

For point clouds too large to ship as a single `points.bin`, the
server **SHOULD** expose an octree-tiled view. Tiles are
addressed `(level, x, y, z)`; each cell is half-open and contains
the points whose centroid falls inside it. A point at level L = 0
sits in tile `(0, 0, 0, 0)` (which covers the whole bbox).

| Method | Path                                                                         | Returns                              |
|--------|------------------------------------------------------------------------------|--------------------------------------|
| GET    | `/v1/reconstructions/{rid}/snapshots/{seq}/tiles/index.json`                 | tile manifest (see below)            |
| GET    | `/v1/reconstructions/{rid}/snapshots/{seq}/tiles/{level}/{x}/{y}/{z}.bin`    | tile bytes (`application/x-sfm-points-v1`) |

`tiles/index.json`:

```json
{
  "bbox_min":   [x, y, z],
  "bbox_max":   [x, y, z],
  "max_level":  4,
  "tile_count": 27,
  "tiles": [
    { "level": 0, "x": 0, "y": 0, "z": 0, "count": 4567, "byte_size": 118784 },
    ...
  ]
}
```

A tile that addresses an empty cell **MUST** return 404. Servers
**MAY** generate tiles lazily on first request and cache them.

Each tile's binary header repeats the **cell's** bbox, not the
parent dataset's, so a client can render a tile without consulting
the index.

#### 6.9.2 Observations (optional)

| Method | Path                                                                              | Returns |
|--------|-----------------------------------------------------------------------------------|---------|
| GET    | `/v1/reconstructions/{rid}/snapshots/{seq}/images/{image_id}/observations`         | `{image_id, count, observations: [...]}` |
| GET    | `/v1/reconstructions/{rid}/snapshots/{seq}/points/{point3d_id}/visibility`         | `{point3d_id, count, observations: [...]}` |

Observation payload (per image):

```json
{
  "point3d_id": <int>,
  "x":          <float>,
  "y":          <float>,
  "kp_idx":     <int>,
  "error":      <float> | null
}
```

Visibility payload (per point):

```json
{
  "image_id": <int | str>,
  "x":        <float>,
  "y":        <float>,
  "kp_idx":   <int>
}
```

If the underlying snapshot has no observations sidecar (the worker
did not emit one), the server **MUST** return 404.

#### 6.9.3 Image similarity [Preview]

For "show me images that look like this one" UX (clustering,
deduplication, sequential matching primer). This surface is Preview
(§1.3): the reference server always serves it but omits it from the
default OpenAPI document unless `SFMAPI_EXPOSE_PREVIEW_APIS=true`.

| Method | Path                                          | Returns                                    |
|--------|-----------------------------------------------|--------------------------------------------|
| GET    | `/v1/datasets/{did}/similarity`               | `{query_image_id, strategy, k, neighbors}` |
| POST   | `/v1/datasets/{did}/similarity:build`         | `dhash` → 200 manifest; `vlad` → 202 + job |

GET query parameters:
- `image_id` (required) — the image to query against.
- `k` (default 5, max 1000) — how many neighbors to return.
- `strategy` (default `dhash`) — one of:
  - **`dhash`** — 64-bit perceptual difference hash. Available
    unconditionally; index built lazily on first query.
  - **`vlad`** — SfM-grade VLAD descriptors (Hamming-style cosine
    distance over L2-normalized 32×128 = 4096-d vectors). The query
    path is **NumPy-only and does not require pycolmap on the API
    process**, but the index must exist — `GET` returns **404** with
    a pointer to `:build` when no `vlad.npz` is present. The build
    requires pycolmap on the worker.
- `include_self` (default `false`) — if true, returns the query image
  with `distance=0` as the first neighbor.

`neighbors` is `[{image_id, distance}, ...]`, sorted ascending by
`distance`. For `dhash` the distance is Hamming over the 64-bit hash
(range `[0, 64]`). For `vlad` the distance is `max(0, 1 - cosine)`
(range `[0, 2]`).

`POST :build`:
- `strategy=dhash` runs synchronously (200 with manifest).
- `strategy=vlad` enqueues a worker job (202 with `Location:
  /v1/jobs/{job_id}`); poll the job for completion.

Implementations **SHOULD** persist the similarity index keyed by the
dataset's `manifest_hash` and rebuild on mismatch.

#### 6.9.4 Pose priors (optional)

For georegistration, GPS-anchored reconstructions, or seeding the
mapper with known camera poses:

| Method | Path                                            | Body            | Returns                     |
|--------|-------------------------------------------------|-----------------|-----------------------------|
| GET    | `/v1/images/{image_id}/pose_prior`              | —               | `PosePrior` or `null`       |
| PUT    | `/v1/images/{image_id}/pose_prior`              | `PosePrior`     | `PosePrior` (echoed)        |
| DELETE | `/v1/images/{image_id}/pose_prior`              | —               | `204`                       |
| GET    | `/v1/datasets/{did}/pose_priors`                | —               | `{"pose_priors": {id: PP}}` |
| PUT    | `/v1/datasets/{did}/pose_priors`                | `{id: PosePrior}` | `{"written": N}`         |

`PosePrior` shape (see §7.2.2): `cam_from_world: Rigid3`, optional
`covariance` (36-float row-major 6×6), optional `gps: GpsCoord`.

When the dataset is mapped via a recipe, every image whose
`pose_prior_json` is non-null is forwarded into the worker's
`MappingInput` as a soft constraint. Servers **MAY** ignore priors if
the underlying mapper does not support them; in that case the prior is
preserved on disk for future runs but not used.

#### 6.9.5 Sim(3) georegistration (optional)

Georegister a reconstruction (e.g., to align it to a GPS frame or to
scale to metric units):

| Method | Path                                      | Body                | Returns   |
|--------|-------------------------------------------|---------------------|-----------|
| POST   | `/v1/reconstructions/{rid}/georegister`   | `GeoregisterRequest`| 202 + job |

`GeoregisterRequest` shape:
`{ mode: "sim3" | "gps", sim3?: Sim3, provider?, backend_options? }`.

- `mode="sim3"` (default) — `sim3` is **required**; the worker applies
  the caller-supplied `Sim3` transform (capability `georegister.sim3`).
  `Sim3` shape: `{ rotation: Rotation, translation: [x, y, z], scale: f }`
  (see §7.2.2).
- `mode="gps"` — `sim3` is **rejected**; the worker *solves* the
  transform from georeferenced inputs (GPS / geo-tags / control points)
  via the backend's `align_reconstruction` (capability
  `georegister.gps`).

Either way the worker reads the latest sealed snapshot, applies the
transform to every camera + 3D point, and **seals a fresh snapshot**
that clients can read the same way they read post-mapping snapshots.
Servers **MUST** return 404 when `recon_id` is unknown and 422 on a
malformed body (including `mode="sim3"` without a `sim3` transform).

#### 6.9.6 Spherical → cubemap conversion (optional)

For VR / Three.js / pinhole-only viewer pipelines there are two
companion endpoints — one operates on the reconstruction, the other
on the source images:

| Method | Path                                                   | Returns      | Operates on    |
|--------|--------------------------------------------------------|--------------|----------------|
| POST   | `/v1/reconstructions/{rid}:toCubemap`                  | 202 + job    | reconstruction |
| POST   | `/v1/datasets/{did}:renderCubemap?face_size={N}`       | 202 + job    | images only    |
| POST   | `/v1/datasets/{did}:projectImages`                     | 202 + job    | images only    |
| POST   | `/v1/datasets/{did}:renderEquirectangular`             | 202 + job    | images only    |
| POST   | `/v1/datasets/{did}:renderPerspective`                 | 202 + job    | images only    |

`POST :renderCubemap` accepts an optional `face_size` query
(64–8192) for the per-face pixel edge length. Output is a directory
under the dataset's workspace. Servers **MUST** return 422 if the
dataset is not marked ``is_spherical=true``.

Projection jobs emit a ``projection_manifest.json`` with generic SfM
fields: ``source_images``, ``output_images``, optional face geometry,
and optional ``derived_dataset`` registration details. The task result
carries ``{output_path, num_files, manifest_path, derived_dataset}``;
when ``output.create_dataset=true`` (default), the server registers the
generated directory as a derived ``Dataset`` for downstream stages.

The core server MAY implement ``projection.equirectangular_to_cubemap``
with a portable pixel engine. Reverse cubemap rendering and perspective
view rendering are contract-only in core; higher-order sampling modes
such as ``cubic`` and ``lanczos`` MAY also require a backend. A backend
MUST advertise ``projection.cubemap_to_equirectangular`` or
``projection.equirectangular_to_perspective`` before those endpoints are
accepted.

`POST :toCubemap` operates on the reconstruction:

Requires the underlying dataset to be marked ``is_spherical=true``;
servers **MUST** return 422 otherwise. The worker re-projects each
panorama into 6 faces and seals a fresh snapshot whose ``rigs.json``
carries the cubemap rig (1 rig × 6 sensors) and ``frames.json``
carries one frame per panorama (each binding 6 sensor-id → image-id
pairs). Clients then read the new snapshot the same way they read
post-mapping snapshots.

The equirectangular camera itself is represented by ``Camera.model ==
"SPHERICAL"`` with empty ``params`` — only ``width`` / ``height``
matter. See §7.2.2.

#### 6.9.7 Pluggable feature extractors (optional)

`FeaturesSpec` is **type-tagged** so a backend can offer multiple
extractors (SIFT, SuperPoint, ALIKED, DISK, R2D2, D2-Net, SOSNet, ...). The
capability flag for each is `features.extract.{type}` — clients gate
on `GET /v1/capabilities` to learn which the backend supports.

```json
{
  "version": 1,
  "type":    "sift" | "superpoint" | "aliked" | "disk" | "r2d2" | "d2net" | "sosnet",
  "provider": "colmap",
  "max_num_features": 8192,
  "use_gpu":          true,
  "backend_options":  { /* provider-specific overrides */ },
  "extractor_options": { /* deprecated compatibility alias */ }
}
```

`POST /v1/datasets/{did}/features` accepts this shape and returns a
stage LRO once the request is syntactically valid and provider routing
is accepted. If the selected backend later lacks
`features.extract.{type}`, the task/job **MUST** fail with
`capability_unavailable` and the same canonical capability name. Inline
or one-shot feature routes that execute during the request **MUST**
return 501 synchronously for unsupported extractors. The legacy
`sift_max_num_features` / `sift_first_octave` fields are accepted as
aliases when `type=="sift"`.

#### 6.9.8 Pair selection + per-pair matchers

Pair selection and per-pair matching are independent shapes
(AIP-202). `POST /v1/datasets/{did}/matches` takes both:

```json
// PairsSpec — which image pairs to match.
{
  "version": 1,
  "strategy": "exhaustive" | "sequential" | "spatial" |
              "vocabtree" | "retrieval" | "from_poses" | "explicit",
  "provider":           "hloc",
  "overlap":            10,                 // sequential
  "vocab_tree_path":    "...",              // vocabtree
  "retrieval_strategy": "dhash" | "vlad" | "netvlad",
  "retrieval_k":        20,
  "overlap_distance_m": 5.0,                // spatial / from_poses
  "max_angle_deg":      45.0,
  "image_pairs": [
    {"image_name1": "a.jpg", "image_name2": "b.jpg"}
  ],
  "pairs_blob_sha": "..."                   // explicit; pair list upload,
  "backend_options": { /* provider-specific pair-selection options */ }
}
```

```json
// MatcherSpec — how to match each pair.
{
  "version": 1,
  "type":    "nn-mutual" | "nn-ratio" | "superglue" | "lightglue" |
             "loftr" | "mast3r",
  "provider":        "hloc",
  "use_gpu":         true,
  "cross_check":     true,
  "max_ratio":       0.8,
  "max_distance":    0.7,
  "backend_options": { /* provider-specific matcher options */ },
  "matcher_options": { /* deprecated compatibility alias */ }
}
```

Capability flags: `pairs.{strategy}` and `matchers.{type}`. The
optional `provider` fields disambiguate implementations only when a
deployment has multiple providers for the same portable capability
(for example COLMAP SIFT and hloc SuperPoint/SuperGlue). For
`strategy="explicit"`, exactly one of `image_pairs` or
`pairs_blob_sha` is required; `pairs_blob_sha` references a finalized
upload containing newline-delimited `image1 image2` rows. The
match-stage request body is `{pairs: PairsSpec, matcher: MatcherSpec}`;
the legacy combined `MatchesSpec` shape was retired.

Portable sfmapi knobs stay as top-level fields. Backend-specific
knobs go in `backend_options` and SHOULD be discovered from
`GET /v1/backend/config-schemas` when that reference endpoint is
available.

#### 6.9.9 Modern export formats [Extension: `export.<format>`]

In addition to `ply | nvm | colmap_text | colmap_bin`, sfmapi
standardizes four wire formats for downstream neural-rendering
pipelines:

| `format`              | Capability flag             | Output                                     |
|-----------------------|-----------------------------|--------------------------------------------|
| `nerfstudio`          | `export.nerfstudio`         | `transforms.json` (NeRFStudio shape)       |
| `instant_ngp`         | `export.instant_ngp`        | `transforms.json` (instant-ngp + aabb)     |
| `gaussian_splatting`  | `export.gaussian_splatting` | `sparse/0/{cameras,images,points3D}.txt`   |
| `kapture`             | `export.kapture`            | `sensors/` + `reconstruction/` directories |

These emitters are pure-Python and **MUST** be available when the
backend can produce a `Reconstruction` (no engine-specific code).

#### 6.9.10 Map merging (optional)

`POST /v1/reconstructions:merge` takes
`{target_recon_id, source_recon_ids, sim3_aligners?}` and seals the
merged result as a fresh snapshot under the target reconstruction.
All sources **MUST** belong to the same project as the target;
`sim3_aligners` is optional and parallel to `source_recon_ids` (use
the identity Sim3 to leave a model unchanged). Capability:
`recon.merge`.

#### 6.9.11 Batch / sequence localization (optional)

Capabilities `localize.batch` and `localize.sequence` are reserved
for backends that exploit cross-query constraints (relative-pose,
motion smoothing). The reference `colmap_mod` backend currently
implements `localize.batch` as N independent
`localize.from_memory` calls.

#### 6.9.12 Video frame extraction (optional)

`POST /v1/projects/{pid}/datasets:fromVideo` with body
`{video_path, fps?, max_frames?}` runs ffmpeg on the worker to extract
keyframes. Result carries `{output_dir, num_frames, fps}` so the
client can register the output as a `local`-source dataset.
Capability: `video.frame_extract` (depends on ffmpeg on the worker's
PATH).

#### 6.9.13 Kapture import (optional)

`POST /v1/projects/{pid}/datasets:importKapture` with body
`{archive_path}` parses an extracted Kapture archive's
`sensors/sensors.txt` and `sensors/records_camera.txt`, returning
`{sensors, records, image_root}` so the client can `POST` a fresh
`local`-source dataset pointing at `image_root`. Capability:
`import.kapture` (pure-Python, always available).

#### 6.9.13a Image-archive import (optional)

`POST /v1/projects/{pid}/datasets:fromArchive` with body
`{blob_sha, name?, camera_model?, intrinsics_mode?, is_spherical?,
image_prefix?}` registers a dataset from a single uploaded image zip,
collapsing the N-per-image registration flow to one call. The zip
**MUST** first ride the normal chunked-upload protocol (§6.3); the
route only enqueues the unpack. The worker decodes the archive
directly from the blob store (in memory for the in-memory backend —
no second tempfile), extracts the image entries, and the server
registers a derived `local`-source dataset. The terminal job's task
carries `{num_images, derived_dataset}`.

Guarantees a conforming server **MUST** honor:

- The *uncompressed* image total is checked against a configurable cap
  (`SFMAPI_ARCHIVE_IMPORT_MAX_BYTES`, generous default) read from the
  zip central directory **before** any entry is decompressed — a zip
  bomb is rejected up front, not after it inflates.
- Any entry whose path is absolute, drive-anchored, or contains a
  `..` segment fails the whole import (no silent relocation).
- `image_prefix` restricts the import to one zip subtree; when unset
  the common image directory is auto-detected and stripped, so a
  `south-building/images/P1.JPG` entry registers as `P1.JPG`.

Capability: `import.archive` (pure-Python, always available).

#### 6.9.14 Pose-prior IMU + timestamps (optional)

`PosePrior` carries optional `timestamp_ns` and `imu` fields:

```json
{
  "cam_from_world": { "rotation": {...}, "translation": [...] },
  "covariance":     null,
  "gps":            null,
  "timestamp_ns":   1700000000000000000,
  "imu": {
    "timestamp_ns": 1700000000000000000,
    "gyro":  [0.01, 0.02, 0.03],
    "accel": [0.10, -9.81, 0.00]
  }
}
```

Capabilities `inputs.imu` and `inputs.timestamps` are advertised by
sfmapi itself (pure storage features, backend-independent).

#### 6.9.15 Bundle-adjustment loss kernels (optional)

`BundleAdjustmentSpec` adds:

```json
{
  "loss_kernel":    "squared" | "huber" | "cauchy" | "soft_l1" | "tukey",
  "loss_threshold": 1.0
}
```

`squared` is the unweighted least-squares default. Any other kernel
**MAY** be ignored by backends that don't expose it; clients
**SHOULD** check `features.x` capability flags only when they care
about the algorithm choice.

#### 6.9.16 Featuremetric BA (optional)

`BundleAdjustmentSpec.mode = "featuremetric"` requests Pixel-Perfect
SfM-style refinement (CNN-feature error, not raw reprojection).
Capability `ba.featuremetric`. Servers without the capability
return 501.

#### 6.9.17 Single-image localization [Extension: `localize.from_memory`]

Localize a query image against a reconstruction:

| Method | Path                                                | Body                  | Returns      |
|--------|-----------------------------------------------------|-----------------------|--------------|
| POST   | `/v1/reconstructions/{recon_id}/localize`           | `{blob_sha, sift?}`   | 202 + job    |

`blob_sha` is the content-address of the (already-uploaded) query
image. `sift` is an optional dict of SIFT extraction overrides.

The job runs SIFT on the query, then `pycolmap.localize_from_memory`
against the reconstruction's largest sealed snapshot. The task's
`outputs_ref` carries a `LocalizationResult`-shaped payload:

```json
{
  "success": true,
  "cam_from_world": { "rotation": {...}, "translation": [...] },
  "num_inliers": 87,
  "inlier_matches": [[12, 4521], [33, 8002]],
  "diagnostics": { "query_path": "...", "sparse_dir": "..." }
}
```

Servers **MUST** return 404 when `recon_id` is unknown and 422 when
`blob_sha` is missing or the wrong length (must be 64 hex chars).

> **`/localize` vs `:relocalize`.** These are distinct operations on
> the same resource. `POST .../localize` is a read-style pose query —
> "where was this image taken?" — and never mutates the
> reconstruction. `POST .../{rid}:relocalize` (§6.9.18) registers
> additional images *into* the existing reconstruction. The naming
> difference is intentional and both spellings are stable.

#### 6.9.18 Portable post-mapping + retrieval stages (optional)

The decomposed pipeline exposes the post-mapping and dataset-prep
stages as standalone routes. Each takes a portable stage spec
(`{ version, provider?, backend_options?, ... }`, `extra="forbid"`),
enqueues a single Task, and returns the canonical 202 envelope with
the resolved `provider` echoed back.

Reconstruction-scoped:

| Method | Path                                            | Body                | Capability          |
|--------|-------------------------------------------------|---------------------|---------------------|
| POST   | `/v1/reconstructions/{rid}:bundleAdjust`        | `BundleAdjustmentSpec` | `ba.{mode}`      |
| POST   | `/v1/reconstructions/{rid}:triangulate`         | `TriangulateSpec`   | `triangulate.retri` |
| POST   | `/v1/reconstructions/{rid}:poseGraphOptimize`   | `PoseGraphSpec`     | `pgo.optimize`      |
| POST   | `/v1/reconstructions/{rid}:export`              | `ExportSpec`        | `export.{format}`   |
| POST   | `/v1/reconstructions/{rid}:relocalize`          | `RelocalizeSpec`    | `relocalize.images` |
| POST   | `/v1/reconstructions/{rid}:undistort`           | `UndistortSpec`     | `image.undistort`   |

Dataset-scoped (operate on the dataset's feature database):

| Method | Path                                       | Body              | Capability          |
|--------|--------------------------------------------|-------------------|---------------------|
| POST   | `/v1/datasets/{did}:buildVocabTree`        | `VocabTreeSpec`   | `index.vocab_tree`  |
| POST   | `/v1/datasets/{did}:configureRig`          | `RigConfigSpec`   | `rigs.configure`    |
| POST   | `/v1/datasets/{did}:estimateTwoView`       | `TwoViewSpec`     | `geometry.two_view` |

`:bundleAdjust` selects its capability from `mode` (`ba.standard` /
`ba.two_stage` / `ba.featuremetric` / `ba.rig`); `:export` from
`format`. Servers **MUST** return 501 with the canonical capability
name when the resolved backend does not advertise it, 404 when
`rid` / `did` is unknown, and 422 on a malformed spec.
`:triangulate` / `:relocalize` / `:undistort` need a local
`image_root`; servers **MUST** return 422 for upload-source datasets
the worker cannot materialize on demand.

`:relocalize` (register more images into the model) is not the same
operation as `POST /v1/reconstructions/{rid}/localize` (§6.9.17),
which only queries a single image's pose and leaves the model
untouched; the two names are intentionally distinct.

### 6.10 Backend extensions [Reference-only]

This route group is shipped by the reference implementation for
backend-native tools and option schemas that are useful to expose but
are not part of the portable sfmapi standard. Conformance test suites
MUST NOT require these endpoints.

| Method | Path | Body / Query | Returns |
|--------|------|--------------|---------|
| GET | `/v1/backend` | `?provider=` | backend identity, runtime versions, extension links |
| GET | `/v1/backend/actions` | `?page_token=&page_size=&include_schemas=false&provider=` | `Page<BackendAction>` |
| GET | `/v1/backend/actions/{action_id}` | `?provider=` | `BackendAction` with schemas |
| POST | `/v1/backend/actions/{action_id}:validate` | `{provider?, inputs}` | validation result |
| POST | `/v1/backend/actions/{action_id}:run` | `{project_id, provider?, inputs}` | 202 + `JobAcceptedResponse` |
| GET | `/v1/backend/config-schemas` | `?page_token=&page_size=&include_schemas=true&provider=` | `Page<BackendConfigSchema>` |
| GET | `/v1/backend/config-schemas/{config_id}` | `?provider=` | `BackendConfigSchema` |
| GET | `/v1/backend/artifact-contracts` | `?page_token=&page_size=&provider=` | `Page<BackendArtifactContract>` |
| GET | `/v1/backend/artifact-contracts/{contract_id}` | `?provider=` | `BackendArtifactContract` |
| GET | `/v1/backend/providers` | `?page_token=&page_size=` | `Page<Provider>` |
| GET | `/v1/backend/routing` | - | provider priority and routing-profile state |

Action ids SHOULD be stable dot-namespaced strings, such as
`colmap.feature_extractor`. Clients MUST treat ids as opaque and
URL-encode them when used as path segments. List responses SHOULD omit
schemas by default; clients can pass `include_schemas=true` or fetch
one action to build forms. `:run` creates a normal Job and uses the
canonical accepted-job envelope with optional `action_id`, `backend`,
and `provider` fields.

Config schema ids are stable dot-namespaced strings, such as
`colmap.features.sift`. Each schema applies to a portable stage
(`features`, `pairs`, `matcher`, `verify`, `mapping`, or
`bundle_adjustment`), an optional portable capability, and an optional
provider. They describe valid keys inside `backend_options`; runtime
paths such as databases, image roots, and output directories are still
server-managed.

Provider discovery is powered by the reference `sfm_hub` plugin
registry and local install state. A clean install MAY return an empty
provider page and still use the configured backend directly. If
several enabled providers can satisfy the same portable stage and no
request-level `provider` or project, workspace, default, or priority
routing rule exists, the reference implementation returns a validation
error instead of choosing one arbitrarily. When a provider is resolved,
portable worker stages execute through the backend factory registered
for that provider alias. Backend action, config-schema, artifact-contract,
artifact-conversion, one-shot, and MCP discovery surfaces accept the
same provider selector when they need to target a specific installed
backend. Combined pair-selection/matching jobs require
`pairs.provider` and `matcher.provider` to resolve to the same provider;
mixed-provider flows should exchange explicit pair or match artifacts
between separate stages.

### 6.11 Admin [Reference-only]

This route group is shipped by the reference implementation for
operator convenience but is **NOT** part of the spec. Auth is a
deployment concern; many operators front sfmapi with their own
identity provider, mTLS sidecar, or reverse proxy. Conformance test
suites **MUST NOT** require it.

| Method  | Path                          | Body                       | Returns                       |
|---------|-------------------------------|----------------------------|-------------------------------|
| POST    | `/v1/admin/api-keys`          | `{tenant_id, name?}`       | `{raw_key, api_key_id, ...}`  |
| GET     | `/v1/admin/api-keys`          | —                          | `[ApiKeyOut]`                 |
| DELETE  | `/v1/admin/api-keys/{kid}`    | —                          | `ApiKeyOut` (revoked)         |
| GET     | `/v1/admin/plugins`           | `?query=&page_token=&page_size=` | `Page<PluginRegistryItem>` |
| GET     | `/v1/admin/plugins/detect-tools` | —                       | external tool detection       |
| GET     | `/v1/admin/plugins/entry-points` | `?load=false`              | installed Python entry points |
| GET     | `/v1/admin/plugins/{plugin_id}` | —                         | manifest + local state        |
| POST    | `/v1/admin/plugins/{plugin_id}:install` | `{method, github_url?, ref?, package_name?, dry_run?, allow_unsafe_execution?, request_id?, provision_runtime?, force?}` | install plan or result |
| POST    | `/v1/admin/plugins/{plugin_id}:enable` | —                     | plugin state                  |
| POST    | `/v1/admin/plugins/{plugin_id}:disable` | —                    | plugin state                  |
| POST    | `/v1/admin/plugins/{plugin_id}:doctor` | —                     | diagnostics                   |
| POST    | `/v1/admin/routing/profiles`  | `{name, routes}`           | routing state                 |
| POST    | `/v1/admin/routing/default`   | `{profile}`                | routing state                 |
| POST    | `/v1/admin/routing/projects/{project_id}` | `{profile}`      | routing state                 |
| POST    | `/v1/admin/routing/workspaces` | `{profile}`               | routing state                 |
| POST    | `/v1/admin/routing/provider-priority` | `{providers: [...]}` | routing state                 |

The `/v1/admin/routing/*` rows are additionally **[Preview]** (§1.3):
the reference server always serves them but omits them from its
OpenAPI document unless `SFMAPI_EXPOSE_PREVIEW_APIS=true`. The API-key
and plugin rows stay in the reference server's default OpenAPI
document (auth and plugin bootstrap are core operator surface).

Plugin installation is an explicit operator action. Public project,
dataset, pipeline, and job endpoints MUST NOT install plugins
implicitly. HTTP install execution is dry-run by default and requires
`allow_unsafe_execution=true`.

---

## 7. Wire formats

### Stage artifact format ids

Stage artifacts separate semantic kind from storage format. `kind`
answers what the artifact represents, such as `matches.verified.v1` or
`reconstruction.sparse.v1`. `artifact_format` answers how bytes should
be interpreted. Core interchangeable formats use versioned ids under
the `sfmapi.*.v1` namespace, for example
`sfmapi.features.local.v1`, `sfmapi.matches.indexed.v1`,
`sfmapi.matches.verified.v1`, and
`sfmapi.reconstruction.sparse.v1`.

Backend-native files **MUST NOT** be added to the core vocabulary.
Backends expose them as namespaced extension format ids through
artifact contracts, for example `colmap.matches.database.v1` or
`hloc.features.h5.v1`. Format conversions **MUST** be explicit in the
backend artifact contract and should say whether the conversion is
lossless.

Artifact conversion is a long-running operation:

| Method | Path | Body | Result |
|--------|------|------|--------|
| GET | `/v1/artifacts/kinds` | - | `Page<ArtifactKind>` |
| GET | `/v1/artifacts/formats` | - | `Page<ArtifactFormat>` |
| POST | `/v1/artifacts:import` | `ArtifactImportRequest` | `StageArtifact` |
| GET | `/v1/artifacts/{artifact_id}` | - | `StageArtifact` |
| GET | `/v1/artifacts/{artifact_id}/content` | `?download=true` | file bytes |
| POST | `/v1/artifacts/{artifact_id}:conversionPlan` | `{provider?, to_format?, accepted_formats?, require_lossless?}` | `ArtifactConversionPlan` |
| POST | `/v1/artifacts/{artifact_id}:convert` | `{provider?, to_format?, accepted_formats?, require_lossless?, to_kind?, name?, options?}` | 202 + `JobAcceptedResponse` |
| POST | `/v1/artifacts/{artifact_id}:validate` | - | `ArtifactValidation` |

`artifacts:import` registers an existing URI without copying bytes.
The server **MUST** persist it as a normal stage artifact owned by a
completed import job/task.

`StageArtifact.uri` is descriptor metadata, not a serving guarantee.
Servers **MUST** advertise `_links.content` only when
`/v1/artifacts/{artifact_id}/content` can serve a local,
sfmapi-managed regular file named by the top-level artifact `uri` or
`path`. Remote URIs, absent top-level URIs, `files[]`-only local paths,
missing local paths, unmanaged local paths, and local directory artifacts
**MUST NOT** advertise `_links.content`. Directory artifact kinds such as
`reconstruction.snapshot`, `reconstruction.sparse.v1`,
`reconstruction.submodel`, and `radiance.snapshot` retain their
server-internal content path for indexing, but their public
`StageArtifact.uri` is `null` when the source is a local directory.

`conversionPlan` accepts either an exact `to_format` or
`accepted_formats` in preference order. `convert` uses the same
selection rules, including `require_lossless=true`, and submits a
normal job whose task kind is `convert_artifact`. A backend that advertises `conversions` in
`list_backend_artifact_contracts()` **MUST** implement
`convert_artifact(input_artifact, output_dir, to_format, to_kind,
options)`. Servers **MUST** reject conversion requests when no
contracted conversion path exists or when `require_lossless=true`
cannot be satisfied. Multi-step paths **MAY** be executed inside one
conversion task by calling the backend once per step.

### 7.1 Binary points: `application/x-sfm-points-v1`

Header (44 bytes, little-endian):

| Offset | Size | Field    | Type       |
|--------|------|----------|------------|
| 0      | 8    | magic    | `b"SFMP3D\x00\x00"` |
| 8      | 4    | version  | uint32 (1) |
| 12     | 8    | count    | uint64     |
| 20     | 12   | bbox_min | 3 × float32 |
| 32     | 12   | bbox_max | 3 × float32 |

Each record (26 bytes, little-endian):

| Offset | Size | Field      | Type       |
|--------|------|------------|------------|
| 0      | 12   | xyz        | 3 × float32 |
| 12     | 3    | rgb        | 3 × uint8  |
| 15     | 1    | _pad       | uint8      |
| 16     | 2    | track_len  | uint16     |
| 18     | 8    | point3d_id | uint64     |

Records **MUST** be ordered by ascending `point3d_id`. This makes the
file a fixed-stride array, so HTTP `Range: bytes=A-B` requests can
fetch arbitrary point ranges without parsing the body.

`points_preview.bin` is the same format, decimated.

### 7.2 Snapshot JSON files

All snapshot JSONs use a single quaternion convention: **Hamilton
`(w, x, y, z)`, scalar first**. Servers **MUST** convert from any
other internal convention (e.g. Eigen's `(x, y, z, w)`) at the wire
boundary. All transforms are expressed as `Rigid3 = { rotation:
{w,x,y,z}, translation: [tx,ty,tz] }`.

#### `cameras.json`

```json
{
  "cameras": [
    {
      "camera_id": 1,
      "model": "SIMPLE_RADIAL",
      "width": 4032,
      "height": 3024,
      "params": [3200.0, 2016.0, 1512.0, 0.012],
      "has_prior_focal_length": false
    }
  ]
}
```

#### `images.json`

```json
{
  "images": [
    {
      "image_id": 1,
      "name": "DSC_0001.jpg",
      "camera_id": 1,
      "cam_from_world": {
        "rotation":    { "w": 1.0, "x": 0.0, "y": 0.0, "z": 0.0 },
        "translation": [0.0, 0.0, 0.0]
      },
      "points2D": [
        { "xy": [320.5, 240.1], "point3d_id": 42 },
        { "xy": [410.0, 198.7], "point3d_id": null }
      ]
    }
  ]
}
```

The `points2D` array index is the keypoint index (`kp_idx`) referenced
by tracks and TwoViewGeometry inlier sets. `point3d_id: null` means
"keypoint not in any 3D track."

#### `rigs.json` (optional — present when the reconstruction has rigs)

```json
{
  "rigs": [
    {
      "rig_id": 1,
      "ref_sensor_id": 0,
      "sensor_from_rig": {
        "0": { "rotation": {...}, "translation": [...] },
        "1": { "rotation": {...}, "translation": [...] }
      }
    }
  ]
}
```

#### `frames.json` (optional — present for multi-camera frames)

```json
{
  "frames": [
    {
      "frame_id": 10,
      "rig_id": 1,
      "rig_from_world": { "rotation": {...}, "translation": [...] },
      "data_ids": { "0": 100, "1": 101 }
    }
  ]
}
```

#### `pose_graph.json` (optional — present after `pgo`)

```json
{
  "pose_graph": {
    "nodes": [ /* ImagePose, points2D omitted */ ],
    "edges": [
      {
        "image_id1": 1,
        "image_id2": 2,
        "cam2_from_cam1": { "rotation": {...}, "translation": [...] },
        "weight": 1.0
      }
    ]
  }
}
```

#### `summary.json`

```json
{
  "models": [
    { "idx": 0, "num_reg_images": 12, "num_points3D": 4567 }
  ],
  "phase": "incremental_register",
  "mean_reproj_err": 1.07
}
```

Servers **MAY** include additional fields; clients **MUST** ignore
unknown ones.

### 7.2.1 Reconstruction-level files (not per snapshot)

Some artifacts track database state, not a frozen reconstruction —
they live at the reconstruction level and are served from
`/v1/reconstructions/{recon_id}/{name}` instead of inside a snapshot.

#### `two_view_geometries.json`

Emitted by the `verify` worker after `verify_matches` completes. The
file enumerates verified geometries between matched image pairs:

```json
{
  "pairs": [
    {
      "image_id1": 1,
      "image_id2": 2,
      "type": "calibrated",
      "num_inliers": 312,
      "F": null,
      "E": [...9 floats row-major...],
      "H": null,
      "inlier_matches": [[0, 1], [3, 4]]
    }
  ]
}
```

`type` is one of: `undefined | degenerate | calibrated | uncalibrated
| planar | panoramic | planar_or_panoramic | watermark | multiple`.
Only the matrix matching the geometry type is populated.

#### `correspondence_graph.json`

The **raw**, pre-verification matches between image pairs as written
by the matcher. Useful for debugging "why didn't this pair survive
verification?" Emitted by the match worker after every match run.

```json
{
  "pairs": [
    {
      "image_id1": 1,
      "image_id2": 2,
      "num_matches": 312,
      "matches": [[0, 5], [3, 8], [10, 12]]
    }
  ]
}
```

`matches` is a flat list of ``(point2d_idx_in_image1,
point2d_idx_in_image2)`` pairs, indexed against the keypoints in
``images.json`` for each image. Empty pairs are omitted from the file.

### 7.2.2 Native scene types (input shapes)

Servers that accept pose priors / georegistration input **MUST**
accept these shapes (see ``app.schemas.api.scene``):

```json
// PosePrior (covariance is row-major 6x6 over rx, ry, rz, tx, ty, tz)
{
  "cam_from_world": { "rotation": {...}, "translation": [...] },
  "covariance": [...36 floats...] | null,
  "gps": { "lat": 37.0, "lng": -122.0, "alt": 10.0,
           "horiz_accuracy_m": 5.0, "vert_accuracy_m": 8.0 } | null
}

// Sim3 (similarity Sim(3) for georegistration)
{
  "rotation":    { "w": ..., "x": ..., "y": ..., "z": ... },
  "translation": [..., ..., ...],
  "scale":       2.5
}
```

#### Spherical (equirectangular) camera

```json
{
  "camera_id": 1,
  "model":     "SPHERICAL",
  "width":     4096,
  "height":    2048,
  "params":    []
}
```

The ``"SPHERICAL"`` model represents a 360°×180° equirectangular
projection. ``params`` **MUST** be empty — only ``width`` / ``height``
matter. Implementations **SHOULD** test ``Camera.is_spherical()``
rather than string-comparing the model name.

### 7.3 ProgressEvent (SSE / WebSocket)

```json
{
  "schema_version": 1,
  "ts":             "2026-05-02T...",
  "job_id":         "01HZ...",
  "task_id":        "01HZ..." | null,
  "seq":            42,
  "kind":           "phase_started" | "phase_progress" | "phase_completed" |
                    "metric" | "snapshot_available" | "log_line" |
                    "warning" | "error",
  ...kind-specific fields
}
```

Phase enum: `feature_extraction, matching, geometric_verification,
incremental_init, incremental_register, incremental_ba,
global_rotation_avg, global_positioning, global_ba, hierarchical_*,
panorama, spherical, bundle_adjust, triangulate, relocalize,
pose_graph_optimize, segment, export, vlad_index`.

SSE clients **SHOULD** use `Last-Event-ID` to resume.

### 7.4 Specs (input shapes)

```json
// FeaturesSpec
{
  "version": 1,
  "type":                  "sift",
  "provider":              null,
  "sift_max_num_features": 8192,
  "sift_first_octave":     -1,
  "use_gpu":               true,
  "seed":                  0,
  "backend_options":       {}
}

// PairsSpec — see §6.9.8.
{
  "version":            1,
  "strategy":           "exhaustive" | "sequential" | "spatial" |
                        "vocabtree" | "retrieval" | "from_poses" | "explicit",
  "provider":           null,
  "overlap":            10,
  "vocab_tree_path":    null,
  "retrieval_strategy": "vlad",
  "retrieval_k":        20,
  "overlap_distance_m": null,
  "max_angle_deg":      null,
  "image_pairs":        null,
  "pairs_blob_sha":     null,
  "pairs_blob_format":  "image_name_pairs_txt",
  "backend_options":    {}
}

// MatcherSpec — see §6.9.8.
{
  "version":         1,
  "type":            "nn-mutual" | "nn-ratio" | "superglue" | "lightglue" |
                     "loftr" | "mast3r",
  "provider":        null,
  "use_gpu":         true,
  "cross_check":     true,
  "max_ratio":       0.8,
  "max_distance":    0.7,
  "backend_options": {},
  "matcher_options": {}
}

// VerifySpec
{
  "version":          1,
  "provider":         null,
  "use_gpu":          true,
  "min_inlier_ratio": 0.25,
  "backend_options":  {}
}

// PipelineSpec is a discriminated union on `kind`:
{ "kind": "incremental",  "version": 1, "provider": null, "backend_options": {}, "min_num_matches": 15, ... }
{ "kind": "global",       "version": 1, "provider": null, "backend_options": {}, "backend": "AUTO", ... }
{ "kind": "hierarchical", "version": 1, "provider": null, "backend_options": {}, "cluster_max_size": 100, ... }
{ "kind": "spherical",    "version": 1, "provider": null, "backend_options": {}, "panorama": true, ... }
```

---

## 8. WebSocket protocol

Endpoint: `/ws/v1/jobs/{job_id}` (Upgrade: websocket).
Optional query: `?last_event_id=N`.

> The WebSocket surface is intentionally **not** described by the
> machine-readable OpenAPI document — OpenAPI cannot express WebSocket
> operations, so SDK codegen never sees this endpoint and generated
> clients ship no helper for it. This section is the normative
> contract for the surface (the reference implementation additionally
> pins behavior with server-side tests). Clients discover the endpoint
> via the documented path pattern above, not via the OpenAPI document;
> the reference implementation also answers a plain HTTP `GET` on the
> same path with a `{ "kind": "ws_endpoint", "ws_url": ... }` hint for
> curl-based discoverability. The surface itself remains optional
> (§10).

Server frames (JSON text):

```json
{ "kind": "hello",            "job_id": "...", "last_event_id": 42 }
{ "kind": "<ProgressEvent>",  ...payload }
{ "kind": "cancel_requested", "force": false }
{ "kind": "terminal",         "status": "succeeded" | ... }
{ "kind": "pong" }
{ "kind": "error",            "message": "..." }
```

Client frames (JSON text):

```json
{ "op": "ping" }
{ "op": "cancel", "force": false }
```

Servers **MUST** close with code `1000` after sending `terminal` and
**MAY** close with `1008` for malformed frames or unknown `op`.

---

## 9. Job semantics

### 9.1 Cache key

Every Task carries
`cache_key = sha256(canonical_json({kind, inputs_hash, params_hash, runtime_version_id}))`.

When the orchestrator sees a Task whose `cache_key` already has a
`succeeded` row, it **MUST** short-circuit: the new Task starts in
`succeeded` with the cached `outputs_ref`, no work is enqueued.

`runtime_version_id` is a server-side identifier of the
`(engine_sha, dependency versions, hardware arch, seed)` tuple. A
server upgrade that changes any of these **MUST** produce a new
`runtime_version_id`.

### 9.2 Cancellation

- `POST /v1/jobs/{jid}:cancel` sets `cancel_requested=true`. Workers
  **MUST** check this flag at every phase boundary and exit cleanly.
  Status transitions from `running` to `cancelled`.
- `POST /v1/jobs/{jid}:cancel?force=true` additionally sets
  `cancel_force=true`. Workers **MAY** then SIGKILL the in-flight
  subprocess and restart. Status transitions to `cancelled_dirty`.
- A `cancelled` Task **MAY** be resumed.

### 9.3 Resume

`POST /v1/jobs/{jid}:resume` resets `(failed | cancelled |
cancelled_dirty)` Tasks to `pending`. `succeeded` Tasks stay (cache
hit). The Job transitions back to `pending`. Mapping tasks
**SHOULD** pick up from the latest checkpoint if the engine
supports it.

### 9.4 Sealed snapshot contract

Workers **MUST** produce snapshots via this protocol:

1. Write to `snapshots/.tmp_{seq}/`.
2. Write a `.complete` marker last.
3. `os.replace` (atomic rename) the temp dir to `snapshots/{seq}/`.
4. Update a `latest` text file via tmp+rename.

Readers (the API serving `GET .../snapshots/{seq}/{name}`)
**MUST**:

- Only enumerate dirs that contain a `.complete` file.
- Treat sealed snapshots as immutable (`Cache-Control: immutable`).
- Never open `database.db` or live `sparse/` files.

---

## 10. Conformance

A *conforming server* **MUST** implement at minimum:

- §6.1 health/meta (except `/metrics` is optional).
- §6.2 projects.
- §6.3 uploads (full chunked flow with idempotency).
- §6.4 datasets (`upload` source kind required; `local` + `s3` optional).
- §6.5 images (single create + list + delete + bytes; `:batchCreate`,
  `thumbnail`, `exif` optional).
- §6.6 stages (features + matches + verify with at least one
  matching `pairs.strategy`).
- §6.7 jobs + SSE events. WebSocket optional.
- §6.8.2 `POST /v1/projects/{pid}/pipelines:run` (the custom typed
  execution preflight route) with the current split behavior:
  the legacy flat SfM chain (`features -> pairs -> matches -> verify -> map`)
  returns 202, while a server that does not advertise
  `pipelines.custom_execution` returns 501 for type-valid native typed
  Processor DAGs. The rest of §6.8.2 — the typed dataflow discovery and
  validation endpoints — is Preview (§1.3) and **MUST NOT** be required.
- §6.9 reconstruction reads + sealed snapshot reads.
- §7.1 binary points format.
- §7.3 `ProgressEvent` v1 schema.
- §9 job semantics: cache short-circuit, cooperative cancel,
  sealed-snapshot contract.

A *conforming server* **MAY** additionally implement:

- §6.8 named pipeline recipe routes
  (`POST /v1/projects/{pid}/pipelines/{recipe}`); this optional item does not
  weaken the Core legacy flat `/pipelines:run` 202 compatibility shape above.
- §6.8.1 one-shot endpoints.
- §6.8.2 typed dataflow discovery + validation (Preview, §1.3).
- §6.8.2 actual typed dataflow job execution behind
  `pipelines.custom_execution`.
- §6.9.3 image similarity (Preview, §1.3).
- §6.8.3 radiance fields / 3D Gaussian Splatting.
- §6.10 backend actions and backend config schemas.
- §6.11 admin / api-keys / plugin hub.
- `local`/`s3` source kinds.
- WebSocket peek+cancel.
- Mask sets / segmentation (mask *input* support; the wire format is a
  §12 future revision item).
- Geo-registration / submodel transforms (§6.9.5).

Dense MVS and mesh / texture generation are **out of scope** by design
— see Appendix D — and a conforming server neither implements nor is
expected to implement them.

Unsupported capability-gated extension endpoints **MUST** return 501
with `capability_unavailable`. A 404 is reserved for missing addressed
resources/artifacts, path variants intentionally omitted from a server,
or reference-only examples that are not part of that deployment's route
surface.

---

## 11. Compatibility

### 11.1 Forward-compat for clients

Clients **MUST**:

- Ignore unknown response JSON fields.
- Ignore unknown `_links` keys.
- Treat unknown `ProgressEvent.kind` values as "log_line"-equivalent
  (i.e., display message if present, otherwise drop silently).
- Tolerate new HTTP status codes within established classes (e.g.
  treat unrecognised 4xx as a client error).

### 11.2 Vendor extensions

Servers **MAY** add fields prefixed `x-` to any response. Clients
**MUST** ignore them unless they specifically opted in to a vendor
extension.

Vendor-specific endpoints **SHOULD** live under `/v1/x-<vendor>/...`.

### 11.3 Deprecation

A server deprecating an endpoint **SHOULD** emit a
`Deprecation: <date>` and `Sunset: <date>` response header per
[RFC 8594][rfc8594] for at least 90 days before removal in a
*major* version bump.

[rfc8594]: https://www.rfc-editor.org/rfc/rfc8594

---

## 12. Open issues / future revisions

The following surfaces are reserved for future minor revisions but
are **not yet** standardized. Servers **MAY** implement them ahead
of standardization under `x-` prefix paths.

- **Submodel comparison / alignment**: metrics between two
  submodels.
- **IMU-only / odometry-only sequence inputs**: extends pose priors
  to standalone trajectory uploads.
- **Streaming SLAM session**: bidirectional WebSocket frame-in /
  pose-out at ~30 Hz (see `streaming_slam_proposal` in the
  reference repo). Touches §8 and would unlock live map reads.

(Octree tiles, observations, similarity, georegistration, cubemap,
pose priors, and modern export formats were originally listed here
as future work; they're now standardized as §6.9 extensions.)

---

## Appendix A. Notable invariants

- The same `(dataset_snapshot_hash, params_hash, runtime_version_id)`
  triple **MUST** always resolve to the same Reconstruction.
- Sealed snapshots are immutable; their files **MUST NOT** ever be
  rewritten.
- Tenant boundary is enforced server-side; cross-tenant access
  returns 404 (not 403) to avoid leaking existence.
- Web-process implementations **SHOULD NOT** import heavy SfM
  engines; engines live in workers.

## Appendix B. Reserved status code semantics

| Status | Meaning in this spec                                         |
|--------|--------------------------------------------------------------|
| 202    | Long-running operation accepted; observe via `Location`.     |
| 304    | `If-None-Match` hit; body is empty.                          |
| 416    | Out-of-range chunk in chunked upload.                        |
| 422    | Schema validation failed (request well-formed but invalid).  |
| 501    | Capability-gated standard extension or typed executor absent.|
| 503    | Underlying engine (e.g. pycolmap) unavailable.               |
| 507    | Storage error (disk full, blob missing).                     |

## Appendix C. Glossary

- **Blob** — content-addressed bytes in the server's blob store.
- **Materialization** — per-job realization of an `ImageSource` as a
  filesystem directory the engine can read.
- **Reconstruction** — a pipeline run; the result of executing a
  `PipelineSpec` against a dataset.
- **SubModel** — one of N sparse models a reconstruction may produce.
- **Snapshot** — a sealed, immutable directory of reconstruction
  artifacts at a given point in time.
- **Recipe** — a named multi-stage pipeline (`incremental` etc.).
- **Cache key** — server-computed hash that identifies a Task's
  inputs + params + runtime; identical key = identical output.

---

## Appendix D. Explicitly out of scope

These pipelines were considered and **excluded** from sfmapi by
design. Each has a different lifecycle, memory shape, or consumer
audience than sparse SfM, and trying to put all three in one spec
makes both consumers and backend authors miserable.

| Excluded | Why | Where it should live |
|---|---|---|
| **Dense MVS** (PatchMatch stereo, depth maps, fused dense clouds, normal maps) | Different memory shape (per-image GB-scale outputs vs sparse-cloud MB-scale), different consumers (renderers vs UX-driving pose lookups), different lifecycle (offline batch vs interactive). | A separate `mvsapi` spec, layered on top of an sfmapi `Reconstruction`. |
| **Mesh / texture generation** (Poisson, Delaunay, texture mapping) | Belongs to a downstream rendering / asset-prep pipeline that consumes both sparse SfM **and** dense MVS. | A separate `meshapi` spec, layered on top of MVS. |
| **Image segmentation / mask generation** | Masks are an *input* to SfM (consumed by the feature extractor with `MaskSet`), not an output. The pipeline that produces them is a separate concern (SAM, semantic segmentation, custom CNN). sfmapi accepts already-produced masks, doesn't generate them. | A `segmapi` spec or any general-purpose image-segmentation service. The mask wire format that the *consumer* side uses is a future revision item (see §12). |
| **Image-quality / aesthetic filters** | Not a structural concern of SfM; a pre-pipeline data-prep step (e.g. cull blurry frames, dedupe near-identical views). | A general-purpose image-curation service. sfmapi accepts whatever images you register. |

If a future deployment wants the full sparse → dense → mesh chain
end-to-end, the recommended pattern is to layer the three spec
domains: clients hit `sfmapi` for sparse SfM, then call into
`mvsapi` (with the resulting `Reconstruction.recon_id`) for dense,
then `meshapi` for surface extraction. Each service stays in its
own resource-lifecycle and dependency footprint.

---

*Comments, issues, and proposed changes:* file under
`https://github.com/SFMAPI/sfmapi/issues` with the `spec` label.
