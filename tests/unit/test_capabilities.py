"""Capability registry + discovery."""

from __future__ import annotations

import pytest

from sceneapi.server.core.capabilities import (
    ALL_KNOWN,
    CAPABILITIES_SCHEMA_VERSION,
    CORE_CAPABILITIES,
    OPTIONAL_CAPABILITIES,
    BackendInfo,
    Capabilities,
    detect_capabilities,
    empty_capabilities,
)

pytestmark = pytest.mark.unit


def test_core_and_optional_are_disjoint() -> None:
    assert set(CORE_CAPABILITIES).isdisjoint(set(OPTIONAL_CAPABILITIES))


def test_all_known_is_union_of_core_and_optional() -> None:
    assert frozenset(CORE_CAPABILITIES + OPTIONAL_CAPABILITIES) == ALL_KNOWN


def test_empty_capabilities_sets_all_core_true() -> None:
    backend = BackendInfo(name="x", version="0.0")
    caps = empty_capabilities(backend)
    for name in CORE_CAPABILITIES:
        assert caps.supports(name), f"core capability {name} should be set True"
    for name in OPTIONAL_CAPABILITIES:
        assert not caps.supports(name), f"optional capability {name} should default False"


def test_supports_returns_false_for_unknown_capability() -> None:
    caps = Capabilities(backend=BackendInfo(name="x", version="0"), features={})
    assert not caps.supports("not.a.real.capability")


def test_as_dict_round_trips_features() -> None:
    backend = BackendInfo(name="b", version="1.2", vendor="me")
    caps = Capabilities(backend=backend, features={"a.b": True, "c.d": False})
    out = caps.as_dict()
    assert out["backend"] == {"name": "b", "version": "1.2", "vendor": "me"}
    assert out["features"] == {"a.b": True, "c.d": False}
    assert out["schema_version"] == CAPABILITIES_SCHEMA_VERSION


def test_schema_version_default_is_v1() -> None:
    caps = Capabilities(backend=BackendInfo(name="x", version="0"))
    assert caps.schema_version == 1
    assert CAPABILITIES_SCHEMA_VERSION == 1


def test_detect_capabilities_includes_schema_version() -> None:
    caps = detect_capabilities()
    out = caps.as_dict()
    assert "schema_version" in out
    assert isinstance(out["schema_version"], int)


def test_detect_capabilities_returns_known_backend() -> None:
    """The conftest registers a stub backend; capability detection
    returns its identity along with the always-on sfmapi-internal
    optional flags."""
    caps = detect_capabilities()
    assert caps.backend.name == "stub"
    for name in CORE_CAPABILITIES:
        assert caps.supports(name)
    # dhash is API-local when the optional image-processing dependency exists;
    # vlad still requires the worker path.
    assert caps.supports("similarity.dhash")
    # pose_priors are pure CRUD — also always available.
    assert caps.supports("pose_priors.read_write")


def test_legacy_spherical_capability_enables_projection_alias() -> None:
    from sceneapi.server.adapters.registry import register_backend
    from sceneapi.server.adapters.stub_backend import StubBackend
    from sceneapi.server.core.capabilities import reset_capabilities_cache

    class LegacySphericalBackend(StubBackend):
        def capabilities(self) -> set[str]:
            return {"spherical.render_cubemap"}

    register_backend("stub", LegacySphericalBackend)
    reset_capabilities_cache()

    caps = detect_capabilities()

    assert caps.supports("spherical.render_cubemap")
    assert caps.supports("projection.equirectangular_to_cubemap")
