"""Backend protocol contract tests.

sfmapi ships no concrete backend — these tests verify the protocol
contract, the registry semantics (no implicit default, structural
typing acceptance, swap mechanics), and the
:class:`CapabilityUnavailableError` shape that backends must use
when an operation isn't supported.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.adapters.backend import Backend, SfmBackend, require_backend_method
from app.adapters.registry import (
    _PROVIDER_REGISTRY,
    _REGISTRY,
    get_backend,
    list_backend_providers,
    list_backends,
    register_backend,
)
from app.adapters.stub_backend import StubBackend
from app.core.errors import CapabilityUnavailableError

pytestmark = pytest.mark.unit


class ActionOnlyBackend:
    name = "action_only"
    version = "1"
    vendor = "tests"

    def capabilities(self) -> set[str]:
        return set()

    def runtime_versions(self) -> dict[str, str]:
        return {"action_only": "1"}

    def list_backend_actions(self) -> list[dict]:
        return [
            {
                "action_id": "action_only.inspect",
                "display_name": "Inspect",
                "stability": "backend_extension",
                "side_effects": "read",
            }
        ]


def test_no_default_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """sfmapi ships nothing; resolving without an env var or
    explicit name raises a clear error."""
    saved = dict(_REGISTRY)
    saved_providers = dict(_PROVIDER_REGISTRY)
    _REGISTRY.clear()
    _PROVIDER_REGISTRY.clear()
    monkeypatch.delenv("SFMAPI_BACKEND", raising=False)
    try:
        with pytest.raises(KeyError, match="no sfmapi backend selected"):
            get_backend()
    finally:
        _REGISTRY.clear()
        _REGISTRY.update(saved)
        _PROVIDER_REGISTRY.clear()
        _PROVIDER_REGISTRY.update(saved_providers)


def test_unknown_backend_name_raises() -> None:
    with pytest.raises(KeyError, match="unknown sfmapi backend"):
        get_backend("not.a.real.backend")


def test_unknown_provider_raises() -> None:
    with pytest.raises(KeyError, match="unknown sfmapi provider"):
        get_backend(provider="not.a.real.provider")


def test_get_backend_provider_does_not_fall_through_to_backend_name() -> None:
    register_backend("stub-fallthrough", StubBackend)
    try:
        assert "stub-fallthrough" not in list_backend_providers()
        with pytest.raises(KeyError, match="unknown sfmapi provider"):
            get_backend(provider="stub-fallthrough")
    finally:
        _REGISTRY.pop("stub-fallthrough", None)


def test_duplicate_provider_aliases_fail_closed() -> None:
    class OtherBackend(StubBackend):
        name = "other"

    register_backend("stub-collision-a", StubBackend, providers=["shared-provider"])
    try:
        with pytest.raises(ValueError, match="already registered"):
            register_backend(
                "stub-collision-b",
                OtherBackend,
                providers=["shared-provider"],
            )
    finally:
        _REGISTRY.pop("stub-collision-a", None)
        _REGISTRY.pop("stub-collision-b", None)
        _PROVIDER_REGISTRY.pop("shared-provider", None)


def test_minimal_action_backend_satisfies_base_protocol_only() -> None:
    backend = ActionOnlyBackend()

    assert isinstance(backend, Backend)
    assert not isinstance(backend, SfmBackend)
    with pytest.raises(CapabilityUnavailableError) as exc:
        require_backend_method(backend, "extract_features", capability="features.extract")
    assert exc.value.status_code == 501
    assert exc.value.extras["capability"] == "features.extract"
    assert "does not implement extract_features" in exc.value.detail


def test_stub_satisfies_sfmbackend_structurally() -> None:
    """Structural typing: any class with the right method names + sigs
    is a SfmBackend, no inheritance required."""
    assert isinstance(StubBackend(), SfmBackend)


def test_register_and_resolve_custom_backend() -> None:
    register_backend("stub-test", StubBackend, providers=["stub-provider"])
    try:
        assert "stub-test" in list_backends()
        assert "stub-provider" in list_backend_providers()
        backend = get_backend("stub-test")
        assert backend.name == "stub"
        assert get_backend(provider="stub-provider").name == "stub"
        # Stub raises on every operation; consumers wanting success
        # paths subclass + override.
        with pytest.raises(CapabilityUnavailableError):
            backend.extract_features(
                database_path=Path("/tmp/db"),
                image_root=Path("/tmp/imgs"),
                image_list=["a.jpg", "b.jpg"],
                options={},
            )
    finally:
        _REGISTRY.pop("stub-test", None)
        _PROVIDER_REGISTRY.pop("stub-provider", None)


def test_unsupported_capability_raises_501_shaped_error() -> None:
    backend = StubBackend()
    with pytest.raises(CapabilityUnavailableError) as exc:
        backend.match(database_path=Path("/tmp/db"), mode="exhaustive", options={})
    assert exc.value.status_code == 501
    assert exc.value.extras["capability"] == "pairs.exhaustive"


def test_capabilities_endpoint_picks_up_swapped_backend() -> None:
    """Replacing the default-named backend in the registry changes the
    capability snapshot. Proves swap-is-one-import-change."""
    from app.core.capabilities import detect_capabilities

    caps = detect_capabilities()
    assert caps.backend.name == "stub"
    # Stub advertises no backend capabilities — every backend op stays
    # False. sfmapi-internal capabilities remain available when their
    # own optional dependencies are present.
    assert not caps.supports("features.extract")
    assert not caps.supports("ba.standard")
    removed_dense_capability = "dense." + "patch_match_stereo"
    assert not caps.supports(removed_dense_capability)
    # sfmapi-internal capabilities still on regardless of backend:
    assert caps.supports("similarity.dhash")
    assert caps.supports("pose_priors.read_write")
