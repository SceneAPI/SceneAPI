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
