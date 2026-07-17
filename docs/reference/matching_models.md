# Matching models & feature types

sfmapi supports **arbitrary matching models** — SIFT, learned local
features (SuperPoint, DISK, ALIKED, …), learned sparse matchers
(LightGlue, SuperGlue), and detector-free / dense matchers (LoFTR, RoMa,
MASt3R). This page explains *where* a given model lives in the contract
and how each backend family is expected to declare and route it.

## Two contract layers

A matching model touches two contracts:

1. **Portable capability + match-format layer** (`sfmapi/server/core/capabilities.py`,
   `sfmapi/server/core/artifacts.py`). Capabilities like `features.extract.superpoint`,
   `matchers.lightglue`, `matchers.loftr` advertise *what* a backend can
   do; the match-artifact formats describe *how* correspondences are
   carried:
   - `sfmapi.matches.indexed.v1` — keypoint-index pairs (detect-then-match)
   - `sfmapi.matches.coordinates.v1` — image-coordinate pairs (detector-free)
   - `sfmapi.matches.dense.v1` — tiled dense / semi-dense fields
   Models outside the fixed capability vocabulary ride the **open
   `backend_options`** envelope (e.g. a free-form `model` string), which
   is how vismatch exposes any of its models.

2. **COLMAP scene-database layer** (`sfmapi/contracts/colmap_db.py`). When matches
   are persisted in a COLMAP SQLite database, the `descriptors.type`
   column records which extractor produced each descriptor set. This is
   an **open registry**, not a closed enum:
   - The *guard* is the invariant: a match may only join two descriptor
     sets of the **same** extractor type (`matches_are_type_compatible`).
     Arbitrary extractor ids are permitted.
   - On disk, `descriptors.type` stores a `colmap_mod`
     `FeatureExtractorType` integer. The known seed is
     `COLMAP_KNOWN_EXTRACTOR_TYPES` (`SIFT=0`, `ALIKED_N16ROT=1`,
     `ALIKED_N32=2`; `UNDEFINED=-1`).
   - An extractor **outside** that seed has two contract-legal homes:
     1. extend the `colmap_mod` enum (a fork C++ change) to store it in
        `descriptors.type`, or
     2. emit `matches.coordinates.v1` / `matches.dense.v1` — which carry
        coordinates directly and never touch the COLMAP keypoint /
        descriptor tables. This is the standard route for detector-free
        models that have no descriptors at all.

So: **detector-based** extractors beyond the seed extend the registry
(and, for COLMAP-native storage, the fork enum); **detector-free / dense**
models bypass descriptor typing entirely via the coordinate/dense
formats.

## What each backend family does

### COLMAP family (`colmap_cli`, `pycolmap`, `colmap_native`)

Bound to `colmap_mod`'s `FeatureExtractorType` enum (SIFT + ALIKED
variants) and the index-based `matches` / `two_view_geometries` tables.
Supporting a *new detector-based* extractor here means widening the
`colmap_mod` enum in C++ and rebuilding; these plugins then inherit it.
They do not need to change to consume the open registry — they already
stamp `descriptors.type` from the enum.

### `hloc`

Produces learned local features (SuperPoint, DISK, …) and sparse
matchers (LightGlue, SuperGlue), and can persist to a COLMAP DB. To align
with the contract:
- stamp `descriptors.type` with its extractor id from the open registry
  rather than forcing the rows to SIFT/ALIKED;
- emit `matches.indexed.v1` keyed to its own keypoints; if the extractor
  id isn't colmap-native-storable, route the matches through
  `matches.coordinates.v1` instead of the COLMAP descriptor tables.

### `vismatch`

The reference for arbitrary matching: any model is selected via a
free-form `model` string in `backend_options` (RoMa, MINIMA-RoMa,
SIFT-LightGlue, ALIKED-LightGlue, XFeat, …). Sparse models that produce
keypoints stamp the open `descriptors.type`; dense / detector-free models
(RoMa, LoFTR) emit `matches.coordinates.v1` and never populate the COLMAP
descriptor tables — exactly the detector-free route above. vismatch needs
the least change because it already operates through the open layer.

## The matching guard

Regardless of family, the cross-extractor invariant is the same and is
the part the contract actually enforces: **a match may only join two
descriptor sets produced by the same extractor type.** A backend must not
match SuperPoint descriptors against SIFT descriptors. The contract
helper `sfmapi.contracts.colmap_db.matches_are_type_compatible(a, b)` expresses
this, and it holds for arbitrary extractor ids — not just the
`colmap_mod` seed.

## See also

- {doc}`job_configuration` — where matcher options live in a request
  (typed `MatcherSpec` vs config-schema vs `backend_options`).
- {doc}`../guides/backend_implementations` — how a backend declares its
  capability surface.
