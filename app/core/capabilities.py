"""Capability discovery — backend-neutral feature flags.

sfmapi is an **API standard for SfM**, not a single-backend service.
Every endpoint that depends on a non-trivial backend feature
(matching, mapping, dense MVS, retrieval, localization, etc.)
declares a capability flag. Servers advertise which flags they
support via ``GET /v1/capabilities``; clients gate UI affordances on
the response. Endpoints that hit an unsupported capability return
``501 Not Implemented`` carrying the canonical capability name —
never a generic 500.

The registry below is the **canonical list** of capability strings.
A backend that adds a feature MUST register a name here so clients
have a stable id to test against. Backend implementations may report
features beyond this list (and clients MUST treat unknown names as
opaque), but anything reported here is part of the standard.

Conformance levels
------------------
- **CORE** capabilities are required of every conforming server. They
  cover project / dataset / image CRUD, uploads, jobs, and SSE
  events. The corresponding routes never raise
  :class:`CapabilityUnavailableError`.
- **OPTIONAL** capabilities are advertised in
  ``Capabilities.features``; clients MUST tolerate any subset.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ---- canonical capability names ------------------------------------------

# CORE: every conforming server provides these. Listed for completeness.
CORE_CAPABILITIES: tuple[str, ...] = (
    "projects.crud",
    "datasets.crud",
    "images.crud",
    "uploads.chunked",
    "jobs.read",
    "events.sse",
    "spec.read",
)

# OPTIONAL feature flags — backend may advertise any subset.
OPTIONAL_CAPABILITIES: tuple[str, ...] = (
    # Per-extractor capability (one per FeatureType)
    "features.extract.sift",
    "features.extract.superpoint",
    "features.extract.aliked",
    "features.extract.disk",
    "features.extract.r2d2",
    "features.extract.d2net",
    # Pair-selection strategies (one per PairStrategy)
    "pairs.exhaustive",
    "pairs.sequential",
    "pairs.spatial",
    "pairs.vocabtree",
    "pairs.retrieval",
    "pairs.from_poses",
    "pairs.explicit",
    # Per-matcher capability (one per MatcherType)
    "matchers.nn-mutual",
    "matchers.nn-ratio",
    "matchers.superglue",
    "matchers.lightglue",
    "matchers.loftr",
    "matchers.mast3r",
    "matches.verify",
    # Mapping
    "map.incremental",
    "map.global",
    "map.hierarchical",
    "map.spherical",
    # Refinement
    "ba.standard",
    "ba.two_stage",
    "ba.featuremetric",
    "triangulate.retri",
    "relocalize.images",
    "pgo.optimize",
    # Multi-session / map operations
    "recon.merge",
    # Multi-image localization
    "localize.batch",
    "localize.sequence",
    # Output
    "export.ply",
    "export.nvm",
    "export.colmap_text",
    "export.colmap_bin",
    "export.nerfstudio",
    "export.gaussian_splatting",
    "export.instant_ngp",
    "export.kapture",
    # Retrieval / similarity
    "similarity.dhash",
    "similarity.vlad",
    # API-layer image helpers that require optional image-processing deps.
    "images.thumbnail",
    # Localization
    "localize.from_memory",
    # Geometry tooling
    "georegister.sim3",
    "projection.equirectangular_to_cubemap",
    "projection.cubemap_to_equirectangular",
    "projection.equirectangular_to_perspective",
    "projection.cubemap_rig",
    "spherical.to_cubemap",
    "spherical.render_cubemap",
    # Inputs
    "pose_priors.read_write",
    "inputs.imu",
    "inputs.timestamps",
    # Data ingest
    "video.frame_extract",
    "import.kapture",
    # Snapshot inspection
    "observations.by_image",
    "observations.by_point",
    # Backend extension action catalog. These flags mean the server
    # can expose backend-native operations without adding each
    # backend-specific command to the portable capability registry.
    "backend.actions",
    "backend.action_schema",
    "backend.action_validate",
    "backend.action_jobs",
    "backend.config_schemas",
    "backend.artifact_contracts",
    # Segmentation
    "segment.sam",
)

ALL_KNOWN: frozenset[str] = frozenset(CORE_CAPABILITIES + OPTIONAL_CAPABILITIES)


@dataclass(frozen=True)
class BackendInfo:
    """Identifying info for the SfM backend powering this server."""

    name: str  # e.g. "colmap_mod", "openmvg", "theia"
    version: str  # backend version string
    vendor: str = ""  # optional human-readable vendor

    def as_dict(self) -> dict[str, str]:
        return {"name": self.name, "version": self.version, "vendor": self.vendor}


CAPABILITIES_SCHEMA_VERSION = 1
"""Wire schema version of the Capabilities envelope.

Bump when the *shape* of the response changes (new top-level keys, a
field type changes, etc.) — NOT when capability flags are added or
flipped (those are negotiated via the dict itself). Clients MAY refuse
to read a higher major than they understand.
"""


@dataclass
class Capabilities:
    """Snapshot of what the current deployment supports.

    ``features`` MUST include every CORE capability (always ``True``).
    OPTIONAL capabilities are present iff supported. Clients MUST
    treat absence of an OPTIONAL key as ``False``.

    ``schema_version`` is the wire-shape version (see
    :data:`CAPABILITIES_SCHEMA_VERSION`); independent of feature flags.
    """

    backend: BackendInfo
    features: dict[str, bool] = field(default_factory=dict)
    schema_version: int = CAPABILITIES_SCHEMA_VERSION

    def supports(self, capability: str) -> bool:
        return bool(self.features.get(capability, False))

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "backend": self.backend.as_dict(),
            "features": dict(self.features),
        }


def empty_capabilities(backend: BackendInfo) -> Capabilities:
    """Start a Capabilities snapshot with all CORE flags set and every
    OPTIONAL flag unset — backend code flips OPTIONAL flags ``True``
    after probing what its underlying engine actually exposes."""
    feats = {name: True for name in CORE_CAPABILITIES}
    for name in OPTIONAL_CAPABILITIES:
        feats[name] = False
    return Capabilities(backend=backend, features=feats)


_CACHED_CAPABILITIES: Capabilities | None = None


def reset_capabilities_cache() -> None:
    """Drop the cached :func:`detect_capabilities` result. Tests +
    backend swaps call this so the next ``detect_capabilities`` /
    ``require`` invocation re-probes the registered backend."""
    global _CACHED_CAPABILITIES
    _CACHED_CAPABILITIES = None


def detect_capabilities() -> Capabilities:
    """Probe the current deployment and report what it can do.

    Asks the active :class:`~app.adapters.backend.Backend` for its
    canonical capability set, then layers on the small set of
    capabilities sfmapi provides itself (for example
    ``pose_priors.read_write`` and optional image helpers) regardless
    of the backend choice.

    Result is cached for the lifetime of the process — backend
    capability sets do not change between requests. Tests + backend
    swaps call :func:`reset_capabilities_cache` to invalidate.
    """
    global _CACHED_CAPABILITIES
    if _CACHED_CAPABILITIES is not None:
        return _CACHED_CAPABILITIES

    from app.adapters.registry import get_backend
    from app.core.config import get_settings

    backend_impl = get_backend()
    versions = backend_impl.runtime_versions()
    backend = BackendInfo(
        name=backend_impl.name,
        version=versions.get("pycolmap_version") or backend_impl.version,
        vendor=backend_impl.vendor,
    )
    caps = empty_capabilities(backend)
    advertised = set(backend_impl.capabilities())
    for name in advertised:
        if name in caps.features:
            caps.features[name] = True
    if caps.features.get("spherical.render_cubemap"):
        caps.features["projection.equirectangular_to_cubemap"] = True
    if caps.features.get("spherical.to_cubemap"):
        caps.features["projection.cubemap_rig"] = True
    action_ids: set[str] = set()
    try:
        from app.adapters.backend_actions import has_backend_actions, list_backend_actions

        if has_backend_actions(backend_impl):
            action_ids = {
                str(action["action_id"])
                for action in list_backend_actions(backend_impl, include_schemas=False)
            }
            caps.features["backend.actions"] = True
            caps.features["backend.action_schema"] = True
            caps.features["backend.action_validate"] = True
            caps.features["backend.action_jobs"] = True
    except Exception:
        pass
    try:
        from app.adapters.backend_config import has_backend_config_schemas

        if has_backend_config_schemas(backend_impl):
            caps.features["backend.config_schemas"] = True
    except Exception:
        pass
    try:
        from app.adapters.backend_artifacts import has_backend_artifact_contracts

        if has_backend_artifact_contracts(backend_impl):
            caps.features["backend.artifact_contracts"] = True
    except Exception:
        pass
    # Backend advertised capabilities not in ALL_KNOWN are silently
    # dropped — log a warning so the integrator knows their
    # capability string never reaches the wire.
    unknown = advertised - caps.features.keys()
    if action_ids:
        unknown = {
            name
            for name in unknown
            if not any(
                name == action_id or name.startswith(f"{action_id}.") for action_id in action_ids
            )
        }
    if unknown:
        from app.core.logging import get_logger

        get_logger("sfmapi.capabilities").warning(
            "backend.capabilities_unknown",
            backend=backend_impl.name,
            unknown=sorted(unknown),
            hint=(
                "add these to app.core.capabilities.ALL_KNOWN or remove them "
                "from the backend's capabilities() set"
            ),
        )
    # sfmapi-internal capabilities — independent of the SfM backend.
    from app.core.optional_deps import has_pillow

    if has_pillow():
        caps.features["similarity.dhash"] = True
        caps.features["images.thumbnail"] = True
    try:
        from app.core.projection_engine import has_projection_engine

        if has_projection_engine():
            caps.features["projection.equirectangular_to_cubemap"] = True
    except Exception:
        pass
    caps.features["pose_priors.read_write"] = True
    # Observations / visibility sidecars are emitted by the snapshot
    # writer regardless of the SfM backend.
    caps.features["observations.by_image"] = True
    caps.features["observations.by_point"] = True
    # Pure wire-format / pure-Python features sfmapi handles itself.
    caps.features["inputs.imu"] = True
    caps.features["inputs.timestamps"] = True
    caps.features["import.kapture"] = True
    # Video frame extraction needs ffmpeg on PATH.
    import shutil as _sh

    if _sh.which("ffmpeg") is not None:
        caps.features["video.frame_extract"] = True
    settings = get_settings()
    if getattr(settings, "sam_available", False):
        caps.features["segment.sam"] = True
    _CACHED_CAPABILITIES = caps
    return caps


def require(capability: str, *, reason: str = "") -> None:
    """Raise :class:`CapabilityUnavailableError` if the current
    deployment doesn't advertise ``capability``."""
    from app.core.errors import CapabilityUnavailableError

    caps = detect_capabilities()
    if not caps.supports(capability):
        raise CapabilityUnavailableError(capability=capability, reason=reason)


__all__ = [
    "ALL_KNOWN",
    "CORE_CAPABILITIES",
    "OPTIONAL_CAPABILITIES",
    "BackendInfo",
    "Capabilities",
    "detect_capabilities",
    "empty_capabilities",
    "require",
    "reset_capabilities_cache",
]
