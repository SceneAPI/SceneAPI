# Implement a backend

sfmapi ships **no concrete SfM engine**. Real reconstructions happen
in a backend package you (or someone else) ships separately. This
page is the contract.

## Backend levels

Backends use structural typing: no inheritance, no metaclass, just the
methods they actually support. The minimum contract is
[`sceneapi.backends.Backend`][prot]: `name`, `version`, `vendor`,
`capabilities()`, and `runtime_versions()`.

Use the smallest level that fits:

| Level | Implement | Best for |
|---|---|---|
| Native actions | `Backend` plus `list_backend_actions()`, `validate_backend_action()`, `run_backend_action()` | vendor CLIs, research repos, workflows that do not expose portable stage artifacts |
| Artifact/stage backend | `Backend` plus one or more stage protocols such as `FeatureBackend`, `MappingBackend`, or `ExportBackend` | tools that can consume/produce sfmapi-compatible databases, models, or snapshots |
| Full SfM backend | `SfmBackend` | engines that support the full portable feature/match/map/refine/export surface |

Workers guard optional stage methods with
`require_backend_method(...)`. If an action-only backend receives a
portable stage request it does not implement, sfmapi returns the
normal `501 CapabilityUnavailableError` instead of an internal
`AttributeError`.

## Portable stage backend

Full portable backends can satisfy [`sceneapi.backends.SfmBackend`][prot] by
structural typing — no inheritance required, no metaclass. A backend
is any class with the right method names, signatures, and an
identity triple (`name`, `version`, `vendor`).

[prot]: ../server/adapters.md

```python
from sceneapi.backends import ProgressReporter

class MyBackend:
    name = "my_backend"
    version = "0.1.0"
    vendor = "me"

    def capabilities(self) -> set[str]:
        # Advertise only what's wired. The /v1/capabilities endpoint
        # surfaces this set; clients use it to decide which stages
        # they can ask for.
        return {"features.extract.sift", "pairs.exhaustive", "matchers.nn-mutual", "ba.standard"}

    def extract_features(self, *, database_path, image_root,
                         image_list, options,
                         progress: ProgressReporter | None = None) -> dict:
        # progress is optional. Older backends can omit it; sfmapi only
        # passes the keyword when the method signature accepts it.
        if progress is not None:
            progress.phase_progress(
                "feature_extraction",
                current=0,
                total=len(image_list),
            )
        ...

    def match(self, *, database_path, mode, options,
              progress: ProgressReporter | None = None) -> dict:
        ...

    def bundle_adjustment(self, **kw) -> dict:
        ...

    def runtime_versions(self) -> dict[str, str]:
        # Returned by /v1/version under backend.runtime_versions.
        # Roll any sha / version / arch into here that should
        # invalidate the cache when it changes.
        return {"engine_sha": "abc123", "cuda_arch": "120"}
```

Only implement a stage method after the corresponding capability is
real. For example, advertise `features.extract.sift` only when
`extract_features(...)` succeeds for valid inputs. Unsupported stage
methods may be omitted entirely. If you choose to implement a method
but reject some modes, raise `CapabilityUnavailableError`.

## Action-only backend

Action-only backends are first-class. They keep `capabilities()` empty
unless they also implement portable stages, and publish backend-native
tools through the action catalog:

```python
class VendorCliBackend:
    name = "vendor_cli"
    version = "0.1.0"
    vendor = "Vendor"

    def capabilities(self) -> set[str]:
        return set()

    def runtime_versions(self) -> dict[str, str]:
        return {"vendor_cli": "2026.1"}

    def list_backend_actions(self, *, include_schemas: bool = False) -> list[dict]:
        schema = {"type": "object", "additionalProperties": False}
        return [{
            "action_id": "vendor_cli.reconstruct",
            "display_name": "Vendor reconstruct",
            "category": "reconstruction",
            "stability": "backend_extension",
            "side_effects": "write",
            "required_capabilities": [],
            "input_schema": schema if include_schemas else None,
        }]

    def validate_backend_action(self, action_id: str, inputs: dict) -> dict:
        return {"action_id": action_id, "valid": True, "errors": [], "normalized_inputs": inputs}

    def run_backend_action(self, action_id: str, inputs: dict, *, workspace=None, progress=None) -> dict:
        ...
```

## Registering at startup

```python
from sceneapi.runtime import register_backend

register_backend("my_backend", MyBackend, providers=["my_backend"])
```

Then either:

- set the env var: `SCENEAPI_BACKEND=my_backend`, or
- pass `name=` to `get_backend("my_backend")` for explicit selection, or
- let sfm_hub resolve a stage `provider` to the registered provider alias.

A common pattern is to register from the package's `__init__.py` so
that `import my_backend` is the only thing the operator needs:

```python
# my_backend/__init__.py
from sceneapi.runtime import register_backend
from .backend import MyBackend

register_backend("my_backend", MyBackend, providers=["my_backend"])
```

### Entry-point plugins

When the backend ships as a Python entry point under
`[project.entry-points."sceneapi.backends"]`, sfm_hub loads it during
lifespan startup. Plugin authors should use the canonical
{class}`sceneapi.backends.Plugin` dataclass to express the entry point
in three lines:

```python
# sfmapi_my_backend/plugin.py
from sceneapi.backends import Plugin

from .backend import MyBackend

MANIFEST = {...}  # PluginManifestDict-shaped

plugin = Plugin(
    manifest=MANIFEST,
    backend_name="my_backend",
    backend_factory=MyBackend,
)
```

The matching `pyproject.toml` declaration is:

```toml
[project.entry-points."sceneapi.backends"]
my_backend = "sfmapi_my_backend.plugin:plugin"
```

`sceneapi scaffold-plugin <id>` generates this shape for you (see
{doc}`../reference/cli`).

`Plugin` supports three modes:

- **Default** (the common case): pass `manifest`, `backend_name`, and
  `backend_factory`. `register()` enumerates the manifest's
  `providers[*].provider_id` and registers the factory under each one,
  with a `TypeError` fallback for older sfmapi versions that don't
  accept `providers=` on the registrar callback.
- **Custom registration via `register_hook=`**: when one entry point
  ships multiple backend factories (the COLMAP family registers four)
  or registers the same backend under multiple alias ids (RealityScan
  Cli), pass `register_hook=your_callable`. When set, `register()`
  delegates to it instead of the default loop, while `manifest` /
  `backend_name` / `backend_factory` still describe the plugin's
  canonical "primary" provider for anything that introspects it.
- **Manifest-only mode** (no in-process backend): for plugins that
  integrate via `container_service` (the splatting backends do this),
  omit `backend_name` and `backend_factory`. `register()` becomes a
  silent no-op; the framework still picks up the manifest via
  `get_plugin_manifest()`.

The plugin's `PluginManifest.providers` list is consulted only as a
fallback for entry points whose `register()` callback doesn't pass
`providers=` through the registrar (legacy / manual `register(registrar)`
functions). When the manifest lists provider ids that don't match any
backend the plugin registered, sfm_hub logs a warning and skips those
provider aliases.

## Reference backend packages

The maintained demo backends follow the same API discovery contract:
portable features in `GET /v1/capabilities`, backend-native commands
in `GET /v1/backend/actions`, and provider-specific stage options in
`GET /v1/backend/config-schemas`.

| Repo | Launcher | Backend family |
|---|---|---|
| `sfmapi_colmap_cli` | `sfmapi-colmap-cli-api` | Original COLMAP CLI |
| `sfmapi_pycolmap` | `sfmapi-pycolmap-api` | PyCOLMAP with COLMAP CLI fallback |
| `sfmapi_colmap` | `sfmapi-colmap-api` | Native COLMAP, PyCOLMAP, and C++ demos |
| `sfmapi_realityscan` | `sfmapi-realityscan-api` | RealityCapture/RealityScan CLI actions |
| `sfmapi_instantsfm` | `sfmapi-instantsfm-api` | InstantSfM Python actions |
| `sfmapi_spheresfm` | `sfmapi-spheresfm-api` | SphereSfM/COLMAP-derived spherical actions |

These packages use distinct Python import packages so agents and
tools can install or inspect more than one backend in the same
environment without module-name collisions.

RealityScan/RealityCapture, InstantSfM, and SphereSfM remain dependent
on their upstream binary, license, model, and solver stacks at runtime.
The C++ bridge e2e classifies those rows separately: proprietary or
missing upstream dependencies are not treated as sfmapi API failures.

## Capability strings

The capability vocabulary is canonical and stable. Backends advertise
the subset they implement; sfmapi reads `capabilities()` once at
boot and caches the result. The full list lives in
`sceneapi.server.core.capabilities.ALL_KNOWN`.

Do not put backend-native commands or vendor tool names in
`capabilities()`. Capabilities are portable sfmapi features. If a
backend has its own tools such as `openmvg.compute_features`,
`hloc.match_pairs`, or `colmap.feature_extractor`, expose them as
backend actions instead.

Common categories:

| Category | Capability strings |
|---|---|
| Feature extraction | `features.extract.{sift, superpoint, aliked, disk, r2d2, d2net, sosnet}` |
| Pair selection | `pairs.{exhaustive, sequential, spatial, vocabtree, retrieval, from_poses, explicit}` |
| Matching | `matchers.{nn-mutual, nn-ratio, superglue, lightglue, loftr, mast3r}` |
| Mapping | `map.{incremental, global, hierarchical, spherical}` |
| Bundle adjustment | `ba.{standard, two_stage, featuremetric, rig}` |
| Radiance / 3DGS | `radiance.{train, evaluate}`, `radiance.metrics.{psnr, ssim, lpips}` |
| Projection | `projection.{equirectangular_to_cubemap, cubemap_to_equirectangular, equirectangular_to_perspective, cubemap_rig}` |
| Other | `relocalize.images`, `pgo.optimize`, `triangulate.retri`, `geometry.two_view`, `image.undistort`, `index.vocab_tree`, `rigs.configure`, `export.{ply, nvm, colmap_text, colmap_bin}`, legacy `spherical.{to_cubemap, render_cubemap}` |

Dense reconstruction, meshing, and vendor-specific tools are not
portable capability strings in the current vocabulary. Expose those
surfaces as backend actions, for example `colmap.patch_match_stereo`
or `openmvg.compute_structure_from_known_poses`, with explicit
schemas under `/v1/backend/actions`.

If you advertise a capability, the corresponding portable stage method
must succeed when called. If a backend does not implement that stage,
omit both the capability and the method. sfmapi converts accidental
portable-stage calls into a clean 501 with the capability name.

Projection-capable backends should prefer one generic
`project_images(operation, input_image_path, output_path, spec)` method.
For compatibility, `equirectangular_to_cubemap` can still be served by
the older `render_spherical_cubemap_images(...)` hook. The portable
cubemap convention is `sfmapi-opencv`: face cameras use OpenCV image
axes, and the canonical face order is `front`, `right`, `back`, `left`,
`up`, `down`.

sfmapi core can provide only the generic pixel path for
`projection.equirectangular_to_cubemap` when the `projection` extra is
installed. It supports `nearest` and `linear` sampling. Higher-order
sampling, reverse cubemap rendering, and equirectangular-to-perspective
views are contract-only in core. Backends that implement those paths
must advertise the corresponding `projection.*` capability and return a
manifest-compatible result.

Projection result dictionaries may include `source_images`,
`output_images`, and `derived_dataset`. If `derived_dataset` is present,
sfmapi registers the output directory as a normal Dataset with Image
rows. If it is omitted and the request keeps `output.create_dataset=true`,
sfmapi derives a conservative dataset manifest from the files written to
the output directory. Requested derived dataset names are collision-safe:
sfmapi appends a task suffix when a name already exists, and task retries
reuse the dataset registered for the same derived output root.

`FeaturesSpec`, `PairsSpec`, and `MatcherSpec` each have an optional
`provider` field. Use it only to disambiguate implementations that
share a portable capability, such as COLMAP SIFT and hloc learned
features. Keep the capability portable (`features.extract.sift`,
`pairs.retrieval`, `matchers.superglue`); put backend-native tool
names in backend actions instead.

Provider resolution is execution-native for portable worker stages. If
validation resolves `provider="hloc"`, the worker calls the backend
factory registered for the `hloc` provider alias instead of blindly
using `SCENEAPI_BACKEND`. Single-backend deployments can ignore provider
aliases and continue using `SCENEAPI_BACKEND`.

The same provider selector is available on backend extension surfaces:
`GET /v1/backend/actions?provider=hloc`,
`GET /v1/backend/config-schemas?provider=colmap_cli`,
`GET /v1/backend/artifact-contracts?provider=hloc`,
`POST /v1/backend/actions/{action_id}:run` with body
`{"provider": "..."}`, and artifact conversion requests with
`"provider": "..."`. MCP tools expose the same optional `provider`
argument for read-only action and conversion discovery.

Pair selection and per-pair matching currently execute inside one
`backend.match(...)` call. `pairs.provider` and `matcher.provider`
therefore must resolve to the same backend for a normal match stage.
To mix providers, materialize one stage as an artifact and pass it into
the next stage through `input_artifacts`.

Stage specs also expose `backend_options`. Use this for
provider-specific knobs that are not part of the portable sfmapi
contract, for example COLMAP's `SiftExtraction.peak_threshold` or an
hloc extractor's model checkpoint. Do not add those knobs as top-level
sfmapi fields unless they are meaningful across backends.

For `pairs.strategy="explicit"`, the worker materializes either inline
`image_pairs` or an uploaded pair-list blob into
`options["pairs"]["pairs_path"]` / `match_list_path` before calling
`backend.match(...)`. The pair-list text format is one `image1 image2`
row per line.

## Feed-forward mapping and radiance init

A feed-forward mapper (a backend implementing the sceneio `Mapper`
contract with `traits.requires_correspondences=False`, capability
`map.feed_forward`) consumes the raw image set directly — no
features/pairs/matcher/verify prefix. The `feed_forward` recipe
(`POST /v1/projects/{pid}/pipelines/feed_forward`, or the one-step
`pipelines:run` chain `["map_feed_forward"]`) runs a single `map` task
and seals a **normal** `Reconstruction`: cameras from the predicted
intrinsics, images from the predicted poses, and a sparse `points3D`
cloud fused from the model's dense per-view output.

Because that output is an ordinary sealed sparse model, the resulting
`recon_id` is a first-class `radiance_train` input — identical to a
COLMAP-produced recon (`POST /v1/projects/{pid}/radiance_fields:train`
with `{"recon_id": "..."}`). No conversion step is required; the
feed-forward → `Reconstruction` → `radiance_train(recon_id)` path is the
bridge into 3D Gaussian Splatting.

`FeedForwardSpec.max_init_points` (optional, `>= 1`) caps the fused
initialization cloud a dense mapper emits into the reconstruction (the
splat-init points). It threads into the neutral
`MappingOptions.extra["max_points"]` the mapper reads — for example the
MapAnything provider subsamples to this cap (default 200k). It is a
mapper-specific hint: mappers that don't fuse (the classical COLMAP path,
the no-op stub) ignore it.

Full **dense per-pixel** splat initialization (feeding every confident
pixel of the dense pointmaps straight into the trainer) is a planned
future enhancement; today the bridge runs through the sparse fused cloud
sealed into the reconstruction.

## Stage output artifacts

Backend methods should return a dict. Data products must be declared
explicitly in an `artifacts` list so clients can choose among multiple
outputs without guessing from backend-local paths:

```python
return {
    "database_path": str(database_path),
    "artifacts": [
        {
            "kind": "matches.indexed.v1",
            "name": "hloc-lightglue",
            "uri": str(matches_path),
            "artifact_format": "sfmapi.matches.indexed.v1",
            "schema_version": 1,
            "summary": {"num_pairs": 12000, "num_matches": 2400000},
            "metadata": {"provider": "hloc", "feature_set": "superpoint"},
        },
        {
            "kind": "matches.database.colmap",
            "name": "colmap-sift",
            "uri": str(colmap_matches_path),
            "artifact_format": "colmap.matches.database.v1",
            "schema_version": 1,
            "summary": {"num_pairs": 9000, "num_matches": 1800000},
            "metadata": {"provider": "colmap", "feature_set": "sift"},
        },
    ],
}
```

Artifact `kind` values are dot-notated identifiers. Reserved core kinds
include `features.local.v1`, `features.global.v1`,
`pairs.image_names.v1`, `matches.indexed.v1`,
`matches.coordinates.v1`, `matches.dense.v1`,
`matches.verified.v1`, `reconstruction.sparse.v1`,
`reconstruction.snapshot`, and `reconstruction.submodel`. Use
same-family namespaced extension kinds for backend-native formats, for
example `features.hloc_h5` or `matches.database.colmap`.
`artifact_format` is the concrete storage or interchange contract.
Portable outputs should use versioned `sfmapi.*.v1` format ids.
Backend-native outputs should use backend-owned ids such as
`hloc.features.h5.v1` or `colmap.matches.database.v1`.

Invalid artifact descriptors fail the task with a clear validation
error. Clients discover outputs with `GET /v1/jobs/{job_id}/artifacts`,
`GET /v1/reconstructions/{recon_id}/artifacts`, or
`GET /v1/artifacts/{artifact_id}`. List endpoints support exact
`kind`, `task_id`, and `name` filters. `GET /v1/artifacts/kinds`
returns the reserved semantic vocabulary; `GET /v1/artifacts/formats`
returns the reserved core interchange formats.

## Stage input artifacts

Clients can pass artifacts back into later stages through
`input_artifacts`, a role-keyed map of artifact references. Core roles
are `features`, `pairs`, `matches`, `verified_matches`, `snapshot`, and
`submodel`; backend-specific roles may use the same dot-key syntax.

```json
{
  "input_artifacts": {
    "features": {
      "artifact_id": "01HZ...",
      "kind": "features.local.v1"
    }
  }
}
```

sfmapi validates tenant scope, optional dataset scope, expected kind,
and core role compatibility before a job is created. All resolved
artifact descriptors are passed to the backend in
`options["input_artifacts"]` so mixed backends can consume richer
formats without adding portable fields.

Artifact rows are created for new task completions. Pre-release
databases are not backfilled automatically; rerun the stage if an older
job needs typed artifact rows.

## Backend artifact contracts

Backends can publish artifact input/output contracts through
`list_backend_artifact_contracts()`. This is the discovery surface for
interchange: it tells a client which portable or backend-native kinds
and format ids a stage accepts and emits.

```python
class MyBackend:
    def list_backend_artifact_contracts(self) -> list[dict]:
        return [
            {
                "contract_id": "my_backend.matcher.lightglue",
                "stage": "matcher",
                "capability": "matchers.lightglue",
                "provider": "my_backend",
                "display_name": "LightGlue match artifacts",
                "accepts": ["features.local.v1", "pairs.image_names.v1"],
                "emits": ["matches.indexed.v1"],
                "accepts_formats": [
                    "sfmapi.features.local.v1",
                    "sfmapi.pairs.image_names.v1",
                ],
                "emits_formats": ["sfmapi.matches.indexed.v1", "hloc.matches.h5.v1"],
                "preferred": "matches.indexed.v1",
                "preferred_format": "sfmapi.matches.indexed.v1",
                "conversions": [
                    {
                        "from_format": "hloc.matches.h5.v1",
                        "to_format": "sfmapi.matches.indexed.v1",
                        "lossless": False,
                        "description": "Descriptor scores are not preserved.",
                    }
                ],
            }
        ]
```

If a backend omits this method, sfmapi derives a conservative contract
from advertised portable capabilities. Explicit contracts are preferred
for backends that can consume native formats or expose conversions.
When `accepts_formats`, `emits_formats`, or `preferred_format` are
omitted for core kinds, sfmapi fills them from the core format
vocabulary.

When a contract advertises `conversions`, the backend must also
implement `convert_artifact(...)`. sfmapi uses this method for
`POST /v1/artifacts/{artifact_id}:convert`:

```python
def convert_artifact(
    self,
    *,
    input_artifact: dict,
    output_dir: Path,
    to_format: str,
    to_kind: str | None = None,
    options: dict | None = None,
) -> dict:
    target = output_dir / "matches.json"
    # Write the requested format, then return normal artifact descriptors.
    return {
        "artifacts": [{
            "kind": to_kind or "matches.indexed.v1",
            "name": "lightglue-indexed",
            "uri": str(target),
            "media_type": "application/json",
            "artifact_format": to_format,
            "schema_version": 1,
        }]
    }
```

Clients can call `POST /v1/artifacts/{id}:conversionPlan` with
`accepted_formats` to let sfmapi choose the shortest conversion path.
If the path has multiple steps, sfmapi calls `convert_artifact()` once
per step inside the conversion task and passes each returned artifact
descriptor into the next call. The API does not silently reinterpret
bytes: missing conversion contracts, lossy conversions when
`require_lossless=true`, and missing `convert_artifact()`
implementations fail before work is queued.

Clients can also register externally produced artifacts with
`POST /v1/artifacts:import`. Imports create a completed
`artifact_import` job/task and then persist a normal `StageArtifact`.
Backends should therefore treat imported artifacts exactly like
worker-produced artifacts: validate `kind` and `artifact_format`, then
consume the advertised `uri` or `files` entries.

## Backend config schemas

Backends can publish JSON Schemas for their `backend_options` through
an optional `list_backend_config_schemas()` method. Clients discover
them with `GET /v1/backend/config-schemas`; when a matching schema is
available, sfmapi uses it to reject unknown keys and simple type or
enum mistakes before creating a job. If no matching schema exists,
options pass through to the backend.

```python
class MyBackend:
    def list_backend_config_schemas(self) -> list[dict]:
        return [{
            "config_id": "my_backend.features.superpoint",
            "stage": "features",
            "capability": "features.extract.superpoint",
            "provider": "my_backend",
            "display_name": "SuperPoint options",
            "option_schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "model_name": {"type": "string"},
                    "max_keypoints": {"type": "integer"},
                },
            },
        }]
```

Descriptor rules:

- `config_id` is a stable, dot-namespaced id such as
  `colmap.features.sift`.
- `stage` is the portable stage: `features`, `pairs`, `matcher`,
  `verify`, `mapping`, `bundle_adjustment`, or `radiance` (for 3DGS / radiance
  training schemas like `radiance.train`).
- `capability` should be the portable capability the schema applies
  to, such as `features.extract.sift` or `map.incremental`.
- `provider` should match the request spec's optional provider
  selector when the schema is provider-specific.
- `option_schema` describes only user-supplied backend options. Do
  not require runtime-managed paths such as databases, image roots, or
  output directories; sfmapi supplies those to worker methods.

Set `additionalProperties: false` on every `option_schema`. The
contract checker rejects schemas without it so sfmapi can catch
misspelled `backend_options` before a job is queued.

### Framework-owned canonical schemas

sfmapi serves a few config schemas itself for any backend that advertises
the matching capability — a single source of truth that backends do not
re-declare:

- **COLMAP stage options** (`colmap.features.sift`, `colmap.pairs.*`
  including `colmap.pairs.from_poses`, `colmap.matcher.sift`, `colmap.verify`,
  `colmap.mapping.*`, `colmap.ba.standard`) are built from the backend's
  `colmap_command_schema()`. The COLMAP-family plugins import this one
  canonical table instead of each keeping a copy.
- **`radiance.train`** is the cross-engine 3DGS training contract: canonical
  knobs `num_gaussians`, `max_resolution`, `init`, and `test_every` (with
  `max_steps` and `eval` as typed `RadianceTrainRequest` fields). Each radiance
  backend maps the canonical name to its native option (`num_gaussians` →
  `max_splats` / `max_cap` / `max_primitives` / `model.cap_max`); genuinely
  engine-specific knobs still pass through the open `backend_options` envelope.

These appear under `GET /v1/backend/config-schemas?provider=<id>` whenever the
provider advertises the capability; the stub backend advertises neither, so the
default catalog is empty.

## Backend actions

Backend actions are for engine-native tools that are useful but not
part of the portable sfmapi standard. They are discovered through
`GET /v1/backend/actions`, validated through
`POST /v1/backend/actions/{action_id}:validate`, and executed as
normal sfmapi jobs through `POST /v1/backend/actions/{action_id}:run`.
The run endpoint returns the same `202 JobAcceptedResponse` envelope
as every other job-submitting endpoint.

Implement these optional methods when your backend exposes native
tools. These methods are enough for an action-only backend:

```python
class MyBackend:
    def list_backend_actions(self) -> list[dict]:
        return [{
            "action_id": "openmvg.compute_features",
            "display_name": "OpenMVG compute_features",
            "category": "features",
            "stability": "backend_extension",
            "side_effects": "write",
            "input_schema": {"type": "object", "properties": {}},
            "required_capabilities": ["features.extract.sift"],
        }]

    def get_backend_action(self, action_id: str) -> dict:
        ...

    def validate_backend_action(self, action_id: str, inputs: dict) -> dict:
        return {"valid": True, "errors": [], "normalized_inputs": inputs}

    def run_backend_action(self, action_id: str, inputs: dict, *, workspace=None, progress=None) -> dict:
        ...
```

Descriptor rules:

- `action_id` is a stable, dot-namespaced id such as
  `colmap.feature_extractor`; do not include `/`.
- `input_schema` and `output_schema` are JSON Schema objects. List
  responses omit them unless the client passes `include_schemas=true`;
  `GET /v1/backend/actions/{action_id}` always includes them when
  available.
- Backend action `required_capabilities` may contain only portable
  public names from `sceneapi.server.core.capabilities.ALL_KNOWN`. Backend-native
  prerequisites belong in `metadata`. Plugin manifest/provider/
  processor `capabilities` are a separate typed-extension vocabulary:
  plugin-declared, provider-covered, contract-id-shaped ids that are
  treated as opaque plugin requirements, not automatically as public
  `/v1/capabilities` features.
- `side_effects` is one of `none`, `read`, `write`, or `unknown`.
  `stability` is one of `stable`, `experimental`,
  `backend_extension`, or `deprecated`.
- `run_backend_action` must return a JSON-serializable dict. Accept
  optional `workspace` and `progress` keywords when the action needs a
  scratch directory or wants to emit progress events.

Use sfmapi's combined contract checker in your backend package so this
split is enforced in CI:

```python
from sceneapi.backends import assert_backend_contract
from my_backend import MyBackend

def test_backend_contract():
    assert_backend_contract(MyBackend())
```

The checker rejects unknown portable capabilities, duplicate
action/config ids, malformed descriptors, non-portable
`required_capabilities`, action/config ids leaked through
`capabilities()`, config schemas for unadvertised capabilities,
runtime-managed options exposed in `backend_options`, and schemas that
cannot catch misspelled keys.

You can also run it without writing a test:

```bash
sceneapi check-backend --import my_backend --backend my_backend
```

## Progress reporting

Backends can report best-effort progress by accepting an optional
keyword-only `progress` argument on long-running methods. The reporter
writes the same `ProgressEvent` stream served by
`GET /v1/jobs/{id}/events` and summarized by
`GET /v1/jobs/{id}/progress`. sfmapi detects support from the method
signature and only passes `progress=` when it is accepted, so adding it
is backwards compatible.

Use `current` / `total` counts instead of pre-rounded percentages
when possible:

```python
def match(self, *, database_path, mode, options, progress=None) -> dict:
    for idx, pair in enumerate(selected_pairs, start=1):
        run_pair(pair)
        if progress is not None:
            progress.phase_progress("matching", current=idx, total=len(selected_pairs))
    return {"num_pairs": len(selected_pairs)}
```

Reporter methods:

| Method | Use for |
|---|---|
| `phase_progress(phase, current, total=None, rate=None)` | Numeric progress inside a known phase |
| `metric(key, value)` | Scalar telemetry such as pairs/sec |
| `warning(message)` | Non-fatal backend warnings |
| `log_line(level, message)` | Human-readable backend log lines |
| `snapshot_available(snapshot_seq, summary)` | Snapshot publication, normally emitted by sfmapi mapping tasks |

For core worker tasks, sfmapi emits the phase start/completion
envelope; backends usually emit intermediate `phase_progress`,
`metric`, `warning`, or `log_line` events. Progress is telemetry, not
control flow. A backend should never fail a job because progress
delivery failed.

## What sfmapi guarantees you

- **No subprocess management for free** — workers run in a process
  the supervisor manages. Lease / heartbeat / cancellation happens
  outside your method.
- **Sealed-snapshot writes** — write your reconstruction artifacts
  under a tempdir; sfmapi atomically renames into `snapshots/{seq}/`
  and signals the API.
- **Cache-key salt** — `runtime_versions()` rolls into the cache key
  for every task. Bump any returned key on engine upgrades.
- **Tenancy isolation** — file paths and DB rows already filtered by
  `tenant_id`. Your methods only see the per-tenant workspace.

## What sfmapi does NOT do

- **Own your engine lifecycle.** Backends declare Python dependencies in
  their package metadata. If they need release assets, native builds, or
  model downloads, they may expose `package.provisioning.provision()` so
  `sceneapi plugins install --method uv` can plan or run that setup.
  Provisioners may return `steps`, `warnings`, `metadata`, `env`, and
  `outputs`; sfmapi serializes environment values only as `env_keys` and
  `redacted_env`, and redacts secret-looking keys such as `TOKEN`, `SECRET`,
  `KEY`, `PASSWORD`, `CREDENTIAL`, or `AUTH`.
- **Configure CUDA.** Backends that need a GPU must check at startup
  and either work in CPU-fallback mode or raise from `__init__`.
- **Validate your dicts.** The `dict` return shape is loose; clients
  validate against the OpenAPI scene artifact schemas if they care.

## Reference: the no-op stub

[`sceneapi.server.adapters.stub_backend.StubBackend`][stub] is the internal no-op reference
that ships in this repo. It exists for tests, ephemeral mode, and
SDK live-server suites. Every method raises
`CapabilityUnavailableError`; `capabilities()` returns the empty
set; `runtime_versions()` returns a single `stub_version` key.

[stub]: ../server/adapters.md

Use it as a template:

```bash
cp -r vendor/your-backend-template my-backend/
# then implement the methods incrementally, advertising each
# capability only after wiring it.
```

## Testing a backend

Reuse sfmapi's contract tests against your registered backend:

```bash
uv run pytest -m "contract" --backend=my_backend
```

…where the `--backend` arg is whatever your test harness wires
through ``sceneapi.runtime.register_backend("name", MyBackend, providers=[...])``
in a conftest. The contract tests assert the protocol shape, not
engine semantics — they catch "forgot to add the new method when
sfmapi added one to the Protocol" regressions.
