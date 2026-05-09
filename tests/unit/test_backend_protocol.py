"""SfmBackend protocol contract tests.

sfmapi ships no concrete backend — these tests verify the protocol
contract, the registry semantics (no implicit default, structural
typing acceptance, swap mechanics), and the
:class:`CapabilityUnavailableError` shape that backends must use
when an operation isn't supported.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.adapters.backend import SfmBackend
from app.adapters.registry import (
    _REGISTRY,
    get_backend,
    list_backends,
    register_backend,
)
from app.adapters.stub_backend import StubBackend
from app.core.errors import CapabilityUnavailableError

pytestmark = pytest.mark.unit


def test_no_default_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """sfmapi ships nothing; resolving without an env var or
    explicit name raises a clear error."""
    saved = dict(_REGISTRY)
    _REGISTRY.clear()
    monkeypatch.delenv("SFMAPI_BACKEND", raising=False)
    try:
        with pytest.raises(KeyError, match="no SfmBackend selected"):
            get_backend()
    finally:
        _REGISTRY.clear()
        _REGISTRY.update(saved)


def test_unknown_backend_name_raises() -> None:
    with pytest.raises(KeyError, match="unknown SfmBackend"):
        get_backend("not.a.real.backend")


def test_stub_satisfies_sfmbackend_structurally() -> None:
    """Structural typing: any class with the right method names + sigs
    is a SfmBackend, no inheritance required."""
    assert isinstance(StubBackend(), SfmBackend)


def test_register_and_resolve_custom_backend() -> None:
    register_backend("stub-test", StubBackend)
    try:
        assert "stub-test" in list_backends()
        backend = get_backend("stub-test")
        assert backend.name == "stub"
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
    # False. sfmapi-internal capabilities (dhash, pose_priors) remain
    # available regardless.
    assert not caps.supports("features.extract")
    assert not caps.supports("ba.standard")
    assert not caps.supports("dense.patch_match_stereo")
    # sfmapi-internal capabilities still on regardless of backend:
    assert caps.supports("similarity.dhash")
    assert caps.supports("pose_priors.read_write")
