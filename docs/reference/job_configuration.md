# Configuring a job

This page is the single place to look when you're building a request and need
to know *where* a knob lives. It complements {doc}`api` (endpoint shapes) and
{doc}`openapi` (machine-readable spec); for **server** environment variables
see {doc}`configuration` instead.

## Where job configuration lives

Three layers, ordered most-to-least portable:

1. **Typed request fields** ({doc}`SDK <../sdk/index>` exposes them with full
   autocomplete). Examples: `RadianceTrainRequest.max_steps`,
   `RadianceEvalConfig.metrics`, `FeaturesSpec.max_num_features`,
   `IncrementalSpec.min_num_matches`. These are *universal-to-all-plugins*
   knobs — sfmapi validates them with Pydantic at request time.
2. **Canonical `radiance.train` config-schema** (radiance/3DGS-universal,
   served via `GET /v1/backend/config-schemas/radiance.train`). Holds the
   splat-universal knobs (`num_gaussians`, `max_resolution`, `init`,
   `test_every`) that aren't typed on `RadianceTrainRequest` but which every
   radiance backend understands. The schema also publishes
   `metadata.native_aliases` per provider so consumers can see how the
   canonical name maps (e.g. `num_gaussians` → `max_splats` for brush,
   `max_cap` for lfs, `max_primitives` for fastergs, `model.cap_max` for
   spirulae, `num_gaussians` for gsplat). Radiance training does **not**
   strict-validate against this schema (engine-specific extras are allowed
   through `backend_options`), but a typo guard rejects close-match
   misspellings like `num_gaussain` so they don't silently waste a GPU run.
3. **Open `backend_options` dict.** Carries genuinely engine-specific knobs
   (e.g. `dataset_path`, `image_root`, `model.primitive`, `learning_rate`).
   SfM stages strict-validate this against the per-provider config-schema
   when one exists with `additionalProperties: false` — so a typo on a
   COLMAP option is a 422; radiance keeps the envelope open per above.

## Per-stage reference

| Stage | Typed spec | Config-schema(s) | Canonical / common knobs |
|---|---|---|---|
| `features` | `FeaturesSpec` (`type`, `max_num_features`, `use_gpu`, `seed`) | `colmap.features.sift` (per-provider; sourced from `colmap_command_schema()`) | `max_num_features` |
| `pairs` | `PairsSpec` (`strategy`, `overlap`, `retrieval_strategy`, `retrieval_k`, `overlap_distance_m`, `max_angle_deg`, …) | `colmap.pairs.{exhaustive,sequential,spatial,vocabtree,explicit,from_poses}` | `retrieval_k` (i.e. `num_matched`), `overlap` |
| `matcher` | `MatcherSpec` (`type`, `use_gpu`, `cross_check`, `max_ratio`, `max_distance`) | `colmap.matcher.sift` · `hloc.matcher` · `vismatch.matcher` (engine model enum) | `type` (matcher kind), `max_ratio` |
| `verify` | `VerifySpec` | `colmap.verify` | |
| `mapping` | `IncrementalSpec` (`min_num_matches`, …) · `GlobalSpec` · `HierarchicalSpec` · `SphericalSpec` | `colmap.mapping.{incremental,global,hierarchical}` · `instantsfm.mapping.global` (instantsfm extras like `disable_depths`, `disable_semantics`) | `min_num_matches` |
| `bundle_adjustment` | `BundleAdjustmentSpec` (`mode`, `max_num_iterations`, …) | `colmap.ba.standard` | `mode` (`standard`/`two_stage`/`featuremetric`/`rig`) |
| `radiance` | `RadianceTrainRequest` (`max_steps`, `eval`, `provider`, `method`) + `RadianceEvalConfig` (`metrics`, `split`, `lpips_net`, …) | `radiance.train` (cross-engine canonical: `num_gaussians`, `max_resolution`, `init`, `test_every`) | `max_steps`, `num_gaussians`, `max_resolution` |

## Discovering a provider's config-schemas

```bash
# list every schema a provider serves
curl -s "$BASE/v1/backend/config-schemas?provider=gsplat" | jq

# fetch one by id (the radiance.train schema carries `metadata.native_aliases`)
curl -s "$BASE/v1/backend/config-schemas/radiance.train?provider=gsplat" | jq
```

The default catalog (no `?provider=` and stub backend) is empty by design;
discovery is per-provider so the surface reflects what *that* engine accepts.

## ID namespaces

Five distinct id classes appear in the contract; the same dotted-looking
string is *not* the same id across them:

| Class | What it identifies | Example | Validated as |
|---|---|---|---|
| **capability** | A portable feature/stage the backend implements (`ALL_KNOWN`) | `features.extract.sift`, `radiance.train`, `pairs.from_poses` | `enum` of `sceneapi/server/core/capabilities.py::ALL_KNOWN` |
| **config_id** | A `BackendConfigSchemaOut` row (option schema for `backend_options`) | `colmap.features.sift`, `radiance.train`, `vismatch.matcher` | `^[A-Za-z0-9][A-Za-z0-9_-]*(\.[A-Za-z0-9][A-Za-z0-9_-]*)+$` |
| **action_id** | A `BackendActionOut` row (engine-native verb) | `colmap.feature_extractor`, `vismatch.list_models` | namespaced (must contain `.`) |
| **provider_id** | A provider/backend identity within a plugin | `colmap`, `gsplat`, `hloc` | `^[A-Za-z0-9][A-Za-z0-9_.-]*$` |
| **method** | A versioned recipe under `RadianceTrainRequest.method` (or stage) | `gsplat.train.default`, `colmap_native.incremental.v2` | free-form (registered per provider) |

> **Heads-up:** `radiance.train` legitimately appears in 3 of the 5 classes
> (as a *capability*, as a *config_id*, and as the implicit method when
> `method` is omitted). The class is determined by the field carrying it, not
> by the string. Likewise `colmap.feature_extractor` is an action_id;
> `colmap.features.sift` is a config_id — the difference is the second dotted
> segment, not the namespace.

## See also

- {doc}`api` — endpoint shapes and request bodies.
- {doc}`openapi` — auto-generated machine-readable spec.
- {doc}`../guides/backend_implementations` — how a backend declares the
  contract surface this page consumes (capability vocabulary, config-schema
  mechanism, framework-owned canonical schemas).
- {doc}`errors` — error envelopes for misconfiguration.
