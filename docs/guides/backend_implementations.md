# Implement a backend

sfmapi ships **no concrete SfM engine**. Real reconstructions happen
in a backend package you (or someone else) ships separately. This
page is the contract.

## The Protocol

Backends satisfy [`app.adapters.backend.SfmBackend`][prot] by
structural typing — no inheritance required, no metaclass. A backend
is any class with the right method names, signatures, and an
identity triple (`name`, `version`, `vendor`).

[prot]: ../server/adapters.md

```python
from app.adapters.backend import ProgressReporter, SfmBackend
from app.core.errors import CapabilityUnavailableError

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

    # Methods you don't support: raise CapabilityUnavailableError.
    def dense_pipeline(self, **_) -> dict:
        raise CapabilityUnavailableError(capability="dense.patch_match_stereo")

    # ...etc; see the SfmBackend Protocol for the full list.

    def runtime_versions(self) -> dict[str, str]:
        # Returned by /v1/version under backend.runtime_versions.
        # Roll any sha / version / arch into here that should
        # invalidate the cache when it changes.
        return {"engine_sha": "abc123", "cuda_arch": "120"}
```

## Registering at startup

```python
from app.adapters.registry import register_backend

register_backend("my_backend", MyBackend)
```

Then either:

- set the env var: `SFMAPI_BACKEND=my_backend`, or
- pass `name=` to `get_backend("my_backend")` for explicit selection.

A common pattern is to register from the package's `__init__.py` so
that `import my_backend` is the only thing the operator needs:

```python
# my_backend/__init__.py
from app.adapters.registry import register_backend
from .backend import MyBackend

register_backend("my_backend", MyBackend)
```

## Capability strings

The capability vocabulary is canonical and stable. Backends advertise
the subset they implement; sfmapi reads `capabilities()` once at
boot and caches the result. The full list lives in
`app.core.capabilities.ALL_KNOWN`.

Do not put backend-native commands or vendor tool names in
`capabilities()`. Capabilities are portable sfmapi features. If a
backend has its own tools such as `openmvg.compute_features`,
`hloc.match_pairs`, or `colmap.feature_extractor`, expose them as
backend actions instead.

Common categories:

| Category | Capability strings |
|---|---|
| Feature extraction | `features.extract.{sift, superpoint, aliked, disk, r2d2, d2net}` |
| Pair selection | `pairs.{exhaustive, sequential, spatial, vocabtree, retrieval, from_poses, explicit}` |
| Matching | `matchers.{nn-mutual, nn-ratio, superglue, lightglue, loftr, mast3r}` |
| Mapping | `map.{incremental, global, hierarchical, spherical}` |
| Bundle adjustment | `ba.{standard, two_stage, featuremetric}` |
| Dense | `dense.patch_match_stereo`, `dense.fusion`, `mesh.{poisson, delaunay}` |
| Other | `relocalize.images`, `pgo.optimize`, `triangulate.retri`, `export.{ply, nvm, txt, bin}`, `spherical.{to_cubemap, render_cubemap}` |

If you advertise a capability, the corresponding method must succeed
when called. If you don't, the method must `raise
CapabilityUnavailableError(capability=...)` so clients see a clean
501 + the capability name in the problem detail.

`FeaturesSpec`, `PairsSpec`, and `MatcherSpec` each have an optional
`provider` field. Use it only to disambiguate implementations that
share a portable capability, such as COLMAP SIFT and hloc learned
features. Keep the capability portable (`features.extract.sift`,
`pairs.retrieval`, `matchers.superglue`); put backend-native tool
names in backend actions instead.

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
- `stage` is the portable stage (`features`, `pairs`, `matcher`,
  `verify`, `mapping`, or `bundle_adjustment`).
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

## Backend actions

Backend actions are for engine-native tools that are useful but not
part of the portable sfmapi standard. They are discovered through
`GET /v1/backend/actions`, validated through
`POST /v1/backend/actions/{action_id}:validate`, and executed as
normal sfmapi jobs through `POST /v1/backend/actions/{action_id}:run`.
The run endpoint returns the same `202 JobAcceptedResponse` envelope
as every other job-submitting endpoint.

Implement these optional methods when your backend exposes native
tools:

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
- `required_capabilities` may contain only portable names from
  `app.core.capabilities.ALL_KNOWN`. Backend-native prerequisites
  belong in `metadata`.
- `side_effects` is one of `none`, `read`, `write`, or `unknown`.
  `stability` is one of `stable`, `experimental`,
  `backend_extension`, or `deprecated`.
- `run_backend_action` must return a JSON-serializable dict. Accept
  optional `workspace` and `progress` keywords when the action needs a
  scratch directory or wants to emit progress events.

Use sfmapi's combined contract checker in your backend package so this
split is enforced in CI:

```python
from app.adapters.backend_contract import assert_backend_contract
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
sfmapi check-backend --import my_backend --backend my_backend
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

- **Install your engine.** Bring your own pycolmap / OpenSfM /
  hloc / custom binary. Backends typically declare them as
  Python deps and let `pip install` resolve.
- **Configure CUDA.** Backends that need a GPU must check at startup
  and either work in CPU-fallback mode or raise from `__init__`.
- **Validate your dicts.** The `dict` return shape is loose; clients
  validate against `app.schemas.api.scene` if they care.

## Reference: the no-op stub

[`app.adapters.stub_backend.StubBackend`][stub] is the reference
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
through `register_backend()` in a conftest. The contract tests
assert the protocol shape, not engine semantics — they catch
"forgot to add the new method when sfmapi added one to the
Protocol" regressions.
