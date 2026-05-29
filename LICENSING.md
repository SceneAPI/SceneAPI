# Licensing

> **Not legal advice.** This explains the project's licensing *intent*
> for integrators. Consult a lawyer for your situation.

sfmapi is licensed under the **Apache License, Version 2.0
(Apache-2.0)**. This applies uniformly to:

- the server (`sfmapi`),
- every backend plugin's wrapper + SDK material (`sfmapi_colmap_cli`,
  `sfmapi_pycolmap`, `sfmapi_colmap`, `sfmapi_hloc`,
  `sfmapi_realityscan`, `sfmapi_instantsfm`, `sfmapi_spheresfm`,
  `sfmapi_vismatch`, and the 3DGS plugins),
- all SDKs (Python, TypeScript, C++),
- the benchmark/conformance tools.

Apache-2.0 was chosen deliberately. The SfM/3DGS ecosystem sfmapi
integrates has converged on permissive licensing (nerfstudio, gsplat,
hloc = Apache; COLMAP, glomap, pycolmap, OpenSfM = BSD), and sfmapi's
goal is to be the *lingua franca* — a thing a CV/ML engineer can
`pip install` without a corporate-legal ticket. Apache-2.0
specifically (over MIT/BSD) for its explicit **patent grant**, which
matters in a multi-contributor ecosystem with a live SfM/3DGS patent
landscape.

Bundled third-party engines keep their own upstream licenses, recorded
per plugin under `LICENSES/` (e.g. COLMAP is BSD-3-Clause). Those
licenses govern the upstream code; Apache-2.0 governs *this project's*
code that wraps it.

## 1. What Apache-2.0 means for integrators

Apache-2.0 is permissive — there is **no copyleft**, so none of these
integration shapes impose source obligations on *your* code:

| You... | Obligation on your code? |
|---|---|
| Fork or modify sfmapi and run it as a service | **No.** Preserve `LICENSE`/`NOTICE` and state significant changes; your modifications need not be released. |
| `import` sfmapi in-process / write a backend plugin and register via `register_backend(...)` | **No.** Your plugin may be any license, including proprietary. |
| Import an sfmapi SDK into your client | **No.** Link it into anything. |
| Run sfmapi as a separate service and call its REST API | **No.** |

In all cases you must comply with Apache-2.0's light terms: keep the
`LICENSE` and `NOTICE`, retain attribution/notices, and note
significant modifications. That's it.

## 2. Third-party engine licenses still apply

sfmapi's Apache grant covers sfmapi's code. It does **not** override
the license of an engine a plugin wraps. The notable case:

### `sfmapi_instantsfm` and its CC-BY-NC upstream

`sfmapi_instantsfm`'s wrapper + SDK material is **Apache-2.0**. But it
wraps upstream InstantSfM (`cre185/InstantSfM`), which upstream
licenses **CC-BY-NC-4.0 (non-commercial)**.

- The Apache grant on the wrapper is unrestricted, like every other
  plugin. sfmapi adds no non-commercial term.
- The non-commercial limitation is **upstream InstantSfM's**, and it
  binds whoever *operates* InstantSfM, independent of sfmapi's
  license. The published package ships only the wrapper and references
  the upstream as a submodule — it does not redistribute the NC source.

Net: use the wrapper under Apache freely; whether you may run
InstantSfM through it for commercial advantage is governed entirely by
upstream CC-BY-NC-4.0, on you as the operator.

## 3. Open core — premium plugins (intent, deferred)

The core — spec, reference backend, SDKs, viewer — is Apache-2.0 and
intended to **stay** that way. Going permissive → restrictive later
reliably burns community trust (the MongoDB/Elastic/HashiCorp/Redis
pattern); this project will not relicense the open core.

Monetization, when it comes, follows the open-core model: **premium
plugins** (e.g. proprietary fusion, hardware-tuned 3DGS) shipped as
*separate* packages under a *separate* commercial license — better
code, not gated core code. Because premium components are separate and
authored in-house, no community contribution is ever relicensed. The
premium-license language will be decided when the first premium
component exists; it is intentionally not specified now.

## 4. Contributing

By contributing you agree your contribution is licensed under
Apache-2.0 (inbound = outbound). Contributions are accepted under a
**DCO sign-off** (`Signed-off-by:` per the Developer Certificate of
Origin) for provenance. No CLA is required: the core stays Apache, and
premium components are separate in-house packages, so the project never
needs to relicense contributed code.
