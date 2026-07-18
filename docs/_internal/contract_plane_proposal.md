# P8 — SceneIO as the Contract Plane (data + procedure contracts)

Locked 2026-07-17 after an adversarially-verified design round (two
competing designs, two independent verifiers). Owner gates:

- **Topology: Design B** — `sceneapi-io` is THE contract plane, with
  import-isolated domain namespaces. SceneMap / SceneMatch / 3DGS stay
  *conforming implementation bundles*. This amends the earlier
  "one contract per repo" phrasing: the per-repo-contract packaging
  (Design A) was rated fatal-as-written by both verifiers (7 dists in
  a 4-deep release train; core pip-depending into plugin repos), and
  its separation is hollow because the shared nouns land in sceneapi-io
  regardless. The vision survives as an *option*: each namespace is
  import-isolated so a domain contract can graduate to its own dist
  later (trigger: stable across N releases + an external implementer).
- **Arrays: numpy is a hard dependency** of sceneapi-io. Contracts use
  `np.ndarray` directly. The load-bearing leaf property is unchanged:
  sceneapi-io imports **nothing from the family** (guard-tested); it
  is no longer stdlib-only.

## Why (evidence)

- `MappingBackend.run_mapping(db_path, sparse_root, kind=...)` is a
  COLMAP adapter signature promoted to contract: requires a feature DB
  a feed-forward model doesn't have, presumes `sparse/N` layout, and
  the closed kind vocabulary (`incremental|global|hierarchical|
  spherical`) has no feed-forward member. `ObservationBackend` is a
  typed cursor over COLMAP's SQLite schema.
- The learned family (MapAnything, DUSt3R, MASt3R, VGGT, Fast3R)
  shares an I/O floor: RGB views in (calibration/pose/depth priors
  optional — only MapAnything consumes them), dense per-view geometry
  + **per-pixel confidence** out, poses predicted or recoverable,
  world frame = first view, scale `arbitrary|normalized|metric`.
  COLMAP inverts the requirements (correspondences required, priors
  optional). A neutral contract must make correspondences optional.

## Target shape (sceneapi-io 0.2.0)

```
sceneapi_io/
  data/        ViewInput, PosedViewSet, CameraIntrinsics | RayMap,
               SE3/Sim3 (+convention tags), PosePrior, DepthMap,
               Pointmap, ConfidenceMap, Mask, FeatureSet,
               CorrespondenceGraph, TrackedPointCloud, FrameMeta
               (scale: arbitrary|normalized|metric + provenance)
  formats/     disk/wire format registry (migrates from core
               artifacts.py vocabulary; ids unchanged — wire is
               Phase-C territory)
  mapping/     Mapper, MapperTraits(requires_correspondences,
               accepts_pose_priors, accepts_depth_priors, ...),
               MappingOptions, MappingResult
  matching/    FeatureExtractor, PairMatcher, GeometricVerifier,
               MatcherTraits(persistent_keypoints: bool)
  testing/     conformance kits (pytest imported lazily/inside
               functions — consumers without pytest stay clean)
  (existing)   points_binary, colmap_db, mapping_input, blobstore,
               imagesource, errors — unchanged
```

Guards: (1) sceneapi_io imports nothing from sceneapi/sfm_hub/app;
(2) `mapping/` and `matching/` never import each other (extraction
option preserved); (3) both may import `data/`.

## Migration (Design B, repaired per verifiers)

0. **Pin hygiene first**: core pins `sceneapi-io>=0.2,<0.3` (today
   the dep is unpinned and absent from uv locks); regenerate locks.
1. SceneIO 0.2.0a: add `data/` (+numpy) — pure addition, zero
   consumers, zero risk.
2. SceneIO 0.2.0b: add `mapping/`, `matching/`, `testing/` + the
   cross-import guards + conformance kits.
3. Core (ships as **0.1.x**, NOT 0.2.0 — avoids the resolver
   dead-window colliding with the scheduled 0.2.0 shim removals):
   re-home DataType/format vocabulary as re-exports of
   `sceneapi_io.{data,formats}`; behavior-identical.
4. Core additive registry deltas: capability ids `map.feed_forward`,
   `match.detector_free`, `keypoints.persistent`; the `map`
   Processor's matches port becomes `required=False`; a feed-forward
   pipeline recipe joins `features→pairs→matches→verify→map` in
   pipelines.py; StubBackend grows v1 twins. **This is the one core
   release MapAnything needs — it does not land "without touching
   core" and the plan says so honestly.**
5. Core dual dispatch: worker tasks prefer `isinstance(backend,
   sceneapi_io.mapping.Mapper)`; v0 Path-protocols remain the
   fallback (sunset decision deferred).
6. SceneMatch 0.2.0: explicit `sceneapi-io` dep; vismatch implements
   FeatureExtractor/PairMatcher natively; hloc via adapter.
7. SceneMap 0.2.0: ColmapMapper adapter over existing run_mapping
   internals (traits: requires_correspondences=True); then de-COLMAP
   core (COLMAP_STAGE_CONFIGS + colmap_actions move to sceneapi_map,
   served via the existing BackendConfigSchemaProvider discovery).
8. SceneMap 0.3.0 — **the proof point**: `sceneapi_map/mapanything/`
   provider inside the existing bundle (no new repo), traits
   requires_correspondences=False. Weights licensing: default to the
   Apache-safe variant (facebook/map-anything-apache); the better
   CC-BY-NC weights are an explicit opt-in flag, NOT a pip extra
   (extras are additive and cannot express exclusivity).
9. **Dense→3DGS handoff (the economic payoff, previously unowned)**:
   a bridge from MappingResult (dense pointmaps+poses) to the splat
   trainers' expected inputs — either a conversion in sceneapi-3dgs
   or a MappingResult→sparse-model exporter — WITHOUT this the
   feed-forward path produces results nothing consumes. Scoped as its
   own step with owner review of the bridge design.
10. Deferred, separate sign-off: refinement/localization/retrieval
    contract modernization (they stay v0 Path-protocols meanwhile);
    3DGS contract namespace; wire exposure of pointmap/depth formats
    (Phase C; register 5.4 requires new evidence — a server emitter —
    to reopen).

## Compat CI

Core's CI gains a lane installing released bundles and running their
conformance kits against core HEAD (owner of cross-repo compatibility;
neither design had one).
