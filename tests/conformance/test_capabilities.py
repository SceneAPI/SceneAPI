"""§3.11 Capability discovery — every conforming server MUST advertise
its CORE capabilities and MUST return 501 for OPTIONAL endpoints it
doesn't support, with the canonical capability name in the body.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.conformance


CORE_FEATURE_NAMES = (
    "projects.crud",
    "datasets.crud",
    "images.crud",
    "uploads.chunked",
    "jobs.read",
    "events.sse",
    "spec.read",
)


async def test_capabilities_endpoint_present(conf_client) -> None:
    resp = await conf_client.get("/v1/capabilities")
    assert resp.status_code == 200, resp.text


async def test_capabilities_advertises_backend_identity(conf_client) -> None:
    body = (await conf_client.get("/v1/capabilities")).json()
    backend = body.get("backend") or {}
    assert isinstance(backend.get("name"), str)
    assert backend["name"]
    assert "version" in backend


async def test_capabilities_includes_all_core_flags(conf_client) -> None:
    body = (await conf_client.get("/v1/capabilities")).json()
    features = body.get("features") or {}
    for name in CORE_FEATURE_NAMES:
        assert features.get(name) is True, f"missing CORE capability {name!r}"


async def test_capability_unavailable_returns_501_with_canonical_name(conf_client) -> None:
    """Optional endpoints whose capability is False **MUST** return
    501 with the canonical capability name in the problem+json
    ``capability`` field. We probe ``dense.patch_match_stereo`` —
    a heavy capability that's unlikely to be advertised by a stripped-
    down deployment. If it IS advertised, the 404 path is also valid
    (recon doesn't exist)."""
    feats = (await conf_client.get("/v1/capabilities")).json().get("features", {})
    if feats.get("dense.patch_match_stereo"):
        pytest.skip("backend advertises dense.patch_match_stereo; probe doesn't apply")
    resp = await conf_client.post("/v1/reconstructions/01HGHOST00000000000000000A/dense")
    assert resp.status_code == 501, resp.text
    body = resp.json()
    assert body.get("capability") in (
        "dense.patch_match_stereo",
        "dense.stereo_fusion",
    )
