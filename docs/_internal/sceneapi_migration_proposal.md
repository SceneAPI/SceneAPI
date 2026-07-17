# SceneAPI Migration Proposal (P7)

Owner intent (2026-07-17): "I want everything in SceneAPI eventually."
This proposal stages that migration. Context: the `SceneAPI` GitHub
org (created 2026-06-01) holds twelve 2 KB name-reservation scaffolds
whose layout matches the post-consolidation family almost 1:1; all
real development sits on local repos pointing at the `SFMAPI` org
(created 2026-05-06), with large unpushed backlogs (core: 84 commits).

The project has already renamed once (3dgsapi → sfmapi, May 2026).
The lesson from that rename and from the `app` → `sfmapi.server` fold
(L44): identity changes are cheap exactly once — before external
consumers exist — and staged shims make them safe.

## Naming layers and their costs

| Layer | Examples | Cost to change | Window |
|---|---|---|---|
| Hosting coordinates | org/repo URLs in manifests, registry, CI checkout refs, uv sources, docs links | Low — every site is enumerated (re-pointed twice this month) | any time |
| Package identity | distribution `sfmapi`, import `sfmapi`, CLI `sfmapi`, env `SFMAPI_*`, entry-point group `sfmapi.backends` | Medium — D4-fold pattern with shims, proven | pre-0.1.0 |
| Wire identity | `SFMAPI-SPEC.md`, format ids `sfmapi.*.v1`, media types `application/x-sfm-*-v1`, RFC 7807 type URIs on `sfmapi.github.io` | High — it is the standard itself | pre-1.0, ideally pre-first-consumer |

## Phase A — Org migration (no code-identity changes)

Move hosting to the SceneAPI org; packages and wire stay `sfmapi`.

1. Create org repos and push local `main`s. Mapping (gate G1):
   - `sfmapi` → `SceneAPI/SceneAPI` (core; scaffold content replaced —
     its publish workflow is retired until the package rename lands)
   - `sfmapi-sdk` → `SceneAPI/SceneSDK`
   - `sfmapi_radiance` → `SceneAPI/3DGS` (`4DGS` stays reserved for
     time-dynamic splats — the `images.time_id` contract extension)
   - `sfmapi_colmap_unified` → `SceneAPI/SceneMap`
   - `sfmapi_vismatch` → `SceneAPI/SceneMatch`
   - Interim own-name pushes (merge candidates later, D3-style):
     `sfmapi_hloc` (→ SceneMatch eventually), `sfmapi_instantsfm`,
     `sfmapi_spheresfm`, `sfmapi_realityscan` (→ SceneMap eventually),
     `sfmapi-bench` (no scaffold; keep name or `SceneBench`)
   - `sfmapi-cpp` → pushed archived (history preservation), stays frozen
   - Superseded repos (5 radiance, COLMAP trio) are NOT migrated;
     their SFMAPI remotes get archived in Phase D
   - `SceneMCP`/`SceneIO`/`SceneModels`/`SceneVision`/`SceneVLM`/
     `SceneAI`/`Learning` scaffolds: untouched (future extractions)
2. Coordinate sweep (one commit per repo): manifest `github_url` +
   runtime-mode repo refs, core bundled registry, CI checkout refs
   (`SFMAPI/sfmapi` → `SceneAPI/SceneAPI`), docs links.
3. Register row locking the mapping; SFMAPI repos left readable with a
   "moved" note pending Phase D.
4. Only then tag 0.0.2 — the release fires in the permanent home.

Effort: ~half a day, mechanical, no behavior change. CI runs on
GitHub for the first time for the repos that gained workflows.

## Phase B — Package identity rename (gate G2) → ships as 0.1.0

Distribution `sceneapi`, import package `sceneapi` (shim `sfmapi`
kept one release, exactly the L44 pattern), CLI `sceneapi`, env
prefix `SCENEAPI_*` (old prefix honored via alias for one release),
entry-point group `sceneapi.backends` (loader reads both groups for
one release). SDK distributions follow (`sceneapi-client` for the
generated SDK). Landing this at 0.1.0 bundles every planned break in
one release: hand-rolled SDK removal (L12), `app` shim removal (L44),
and the rename shims' introduction.

PyPI note: the scaffold README only *documents* `pip install
SceneAPI`; nothing is published, so the PyPI name is NOT actually
reserved. Publishing a 0.0.x placeholder under the target name(s)
should happen early in Phase B (or immediately, gate G4).

## Phase C — Wire identity (gate G3)

Spec renamed (`SCENEAPI-SPEC.md`), RFC 7807 type URIs and docs move
to the SceneAPI Pages site, format-id namespace `sfmapi.*.v1` →
`sceneapi.*.v1` with conformance/fixture updates. Media types
`application/x-sfm-*-v1` may stay — "sfm" is semantic (the SfM
domain), not the brand. Requires a `SceneAPI/SceneAPI.github.io`
repo; the SFMAPI Pages site redirects until Phase D.

## Phase D — Decommission SFMAPI

Archive all SFMAPI org repos with pointer READMEs; keep the org name
parked. Delete nothing.

## Rejected alternative

Big-bang rename (org + packages + wire in one pass before any push):
maximum churn while the 84-commit backlog is still unpushed, and it
couples a mechanical hosting move to two identity decisions that
deserve their own review. Staging costs one extra coordinate sweep.


---

## LOCKED 2026-07-17 (gates answered) — execution spec

G1 = all scaffold names now. G2 = package rename immediately.
G3 = wire identity waits for Phase C. G4 = execute now + reserve PyPI.
Consequence: this ships as **0.1.0** (not 0.0.2): the rename lands
with the already-planned 0.1.0 removals.

### Naming scheme (locked)

| Repo (SceneAPI org) | Distribution | Import package | Contents / providers (entry-point names UNCHANGED) |
|---|---|---|---|
| `SceneAPI` | `sceneapi` | `sceneapi` (shim `sfmapi` for one release) | core server; CLI `sceneapi` (+`sfmapi` alias script one release) |
| `SceneSDK` | `sceneapi-client` (generated py) + `@sceneapi/client` (ts) | `sceneapi_client_gen` | SDK repo; hand-rolled python SDK DELETED (was due at 0.1.0) |
| `3DGS` | `sceneapi-3dgs` | `sceneapi_3dgs` (from `sfmapi_radiance`) | brush, gsplat, fastergs, lfs, spirulae |
| `SceneMap` | `sceneapi-map` | `sceneapi_map` | colmap {native,pycolmap,cli} + instantsfm + spheresfm + realityscan (merge) |
| `SceneMatch` | `sceneapi-match` | `sceneapi_match` | vismatch + hloc (merge) |
| `SceneBench` | `sceneapi-bench` | `sceneapi_bench` | created (no scaffold existed) |
| `sfmapi-cpp` | — | — | pushed as-is and archived (frozen history) |

- Env prefix `SCENEAPI_*`; `SFMAPI_*` honored via a construction-time
  alias shim (DeprecationWarning) for one release.
- Entry-point group `sceneapi.backends`; loader reads the legacy
  `sfmapi.backends` group too for one release.
- The `app` compat shim (L44) is REMOVED in this release as scheduled.
- Wire identity unchanged (G3): `SFMAPI-SPEC.md` name, `sfmapi.*.v1`
  format ids, `x-sfm-*` media types, sfmapi.github.io error URIs all
  stay until Phase C.
- Version: core + SDK dists 0.1.0; plugin dists 0.1.0 (fresh names).
- Org scaffolds are absorbed by `merge -s ours` of the scaffold remote
  into local history, then a normal push (no force).
- PyPI: placeholder/real publishes reserve `sceneapi`,
  `sceneapi-client`, `sceneapi-3dgs`, `sceneapi-map`,
  `sceneapi-match`, `sceneapi-bench`; requires PyPI credentials or a
  configured trusted publisher — attempted, reported if blocked.

### Execution order

1. **W7 core rename** (exclusive): sfmapi->sceneapi import/dist/CLI/
   env/entry-point group + shims; drop `app` shim + hand-rolled SDK
   consumers; version 0.1.0; full suite.
2. **W8 parallel**: SceneMatch merge; SceneMap merge; 3DGS + SDK +
   bench renames — all against the renamed core.
3. **W9**: coordinate sweep to SceneAPI/<repo> URLs (manifests,
   bundled registry, CI checkout refs, docs), org pushes, SFMAPI
   "moved" notes, PyPI reservation, register rows.

---

## Execution log

### W7 — core package rename (done 2026-07-17, register L45)

- core `6321b2b` — feat!: rename the package identity, sfmapi becomes
  sceneapi (0.1.0). Import/dist/CLI/env/entry-point group renamed with
  the one-release shims; `app` shim + hand-rolled-SDK consumers
  dropped; full suite green.
- core `aefaf4c` — refactor(sdk-refs): follow the SDK rename
  (`sceneapi_client_gen`, `@sceneapi/client`).

### W8 — family merges/renames (done 2026-07-17, register L46)

All executed against the renamed core; provider ids, entry-point
names, and console-script names unchanged; plugin dists 0.1.0.

- SceneMatch (vismatch + hloc → `sceneapi-match`):
  `SceneMatch@83d2de7`.
- SceneMap (COLMAP {native,pycolmap,cli} via `sfmapi_colmap_unified`
  + instantsfm + spheresfm + realityscan → `sceneapi-map`):
  `SceneMap@15376c4` (scaffold), `05c1817` (merge), `e155968`
  (suite port).
- 3DGS (`sfmapi_radiance` → `sceneapi-3dgs`, providers brush /
  gsplat / fastergs / lfs / spirulae): `sfmapi_radiance@ecd2f41`
  (manifest re-point), `44ed1e0` (rename), `ffeb521` (format).
- bench (`sfmapi-bench` → dist `sceneapi-bench`):
  `sfmapi-bench@4612829`.
- SDK repo: `sfmapi-sdk@63998fe` (generated Python SDK →
  `sceneapi_client_gen` / `sceneapi-client`), `2f1d8b0` (hand-rolled
  Python SDK + CLI removed at 0.1.0 as scheduled, L12), `db8b0b2`
  (`@sfmapi/client` → `@sceneapi/client`).

### W9 — coordinate sweep (in flight)

- Core bundled registry: all 13 `sfm_hub/registry/backends/*/`
  manifests re-pointed at the plugin repos' committed manifests
  (`sceneapi-{map,match,3dgs}` + `SceneAPI/{SceneMap,SceneMatch,3DGS}`
  coordinates); SceneMap's W8 gate test unskipped
  (`SceneMap@6f22b81`). CI checkout refs → `SceneAPI/SceneSDK`;
  deploy/docs/README org links flipped (wire stay-list untouched).
  SDK regen + drift-table rework: `sfmapi-sdk@be960af`.
- Remaining: org pushes, SFMAPI "moved" notes, PyPI reservation.

### Post-migration note — 2026-07-17

- The frozen C++ parity repo was renamed `sfmapi-cpp` → **`sceneapi-cpp`**
  (GitHub `SceneAPI/sceneapi-cpp`, still archived; local dir + git remote
  updated). Register mentions of `../sfmapi-cpp` / `sfmapi-cpp@9aedf30`
  refer to that repo's pre-rename identity. SceneBench's conformance
  default path and `--sceneapi-cpp-root` option follow the new name
  (`sfmapi-bench@da93d4e`). The core repo's local dir remains `sfmapi`
  (only the package became `sceneapi`).
