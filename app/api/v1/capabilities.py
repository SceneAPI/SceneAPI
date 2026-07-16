"""``GET /v1/capabilities`` — backend identity + feature flags.

Clients should call this once at startup and use the result to gate
UI affordances. See :mod:`app.core.capabilities` for the canonical
feature-name registry.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.capabilities import detect_capabilities
from app.schemas.api.capabilities import BackendInfoOut, CapabilitiesOut

router = APIRouter(tags=["capabilities"])


@router.get("/capabilities", response_model=CapabilitiesOut)
async def capabilities() -> CapabilitiesOut:
    """Discovery: backend identity + feature flags this deployment exposes.

    Clients MUST hit this once at startup and cache the result for
    the duration of the connection. Use it to gate UI affordances and
    short-circuit against a 501 round-trip on unsupported operations.

    Feature names
    -------------
    The ``features`` map keys are dot-notated, mirroring AIP-style
    capability namespaces:

    - ``features.extract.{type}`` — feature extractor types
      (``sift`` | ``superpoint`` | ``aliked`` | ...).
    - ``matchers.{type}`` — per-pair matcher implementations.
    - ``pairs.{strategy}`` — pair-selection strategies
      (``exhaustive`` | ``vocabtree`` | ``retrieval`` | ...).
    - ``map.{kind}`` — mapping stages (``incremental`` |
      ``global`` | ``hierarchical`` | ``spherical``).
    - ``ba.{mode}`` — bundle-adjustment modes.
    - ``projection.{kind}``, ``georegister.{mode}``, the closed radiance
      keys (``radiance.train``, ``radiance.evaluate``,
      ``radiance.metrics.psnr``, ``radiance.metrics.ssim``,
      ``radiance.metrics.lpips``), and other closed sfmapi namespaces.

    Backend-native or out-of-scope commands such as dense MVS and mesh
    generation are exposed through ``/v1/backend/actions``, not as
    portable capability families.

    Absence rule
    ------------
    A feature key absent from the map means **unsupported** by this
    deployment. Endpoints that require an unsupported feature return
    ``501 capability_unavailable`` with the canonical name in
    ``capability``. Never assume unlisted keys are ``true``; SDK shims
    use :func:`supports(name)` (Python / TS / C++) which checks for
    the exact key + truthy value.
    """
    caps = detect_capabilities()
    return CapabilitiesOut(
        schema_version=caps.schema_version,
        backend=BackendInfoOut(
            name=caps.backend.name,
            version=caps.backend.version,
            vendor=caps.backend.vendor,
        ),
        features=dict(caps.features),
    )
