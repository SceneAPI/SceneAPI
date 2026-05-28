"""Tests for the canonical sfmapi.backends.Plugin base class — the
dataclass shape every [sfmapi.backends] entry point converged on,
factored out so plugin authors don't re-implement the same boilerplate.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import Any

import pytest

from sfmapi.backends import Plugin


def _manifest_with_providers(*provider_ids: str) -> dict[str, Any]:
    return {
        "plugin_id": "demo",
        "providers": [{"provider_id": pid} for pid in provider_ids],
    }


class _DemoBackend:
    name = "demo"


def test_get_plugin_manifest_returns_underlying_dict() -> None:
    manifest = _manifest_with_providers("demo")
    plugin = Plugin(
        manifest=manifest,
        backend_name="demo",
        backend_factory=_DemoBackend,
    )
    # Identity, not just equality: the helper should not copy.
    assert plugin.get_plugin_manifest() is manifest


def test_register_threads_providers_kwarg_on_modern_registrar() -> None:
    plugin = Plugin(
        manifest=_manifest_with_providers("alpha", "beta"),
        backend_name="demo",
        backend_factory=_DemoBackend,
    )
    calls: list[tuple[str, Any, dict[str, Any]]] = []

    def modern_register(name: str, factory: Any, **kwargs: Any) -> None:
        calls.append((name, factory, kwargs))

    plugin.register(modern_register)
    assert calls == [("demo", _DemoBackend, {"providers": ["alpha", "beta"]})]


def test_register_falls_back_when_registrar_rejects_providers_kwarg() -> None:
    plugin = Plugin(
        manifest=_manifest_with_providers("alpha"),
        backend_name="demo",
        backend_factory=_DemoBackend,
    )
    calls: list[tuple[str, Any]] = []

    def legacy_register(name: str, factory: Any) -> None:
        # No **kwargs -- providers= will raise TypeError; the plugin's
        # fallback path must re-issue with the 2-arg shape.
        calls.append((name, factory))

    plugin.register(legacy_register)
    assert calls == [("demo", _DemoBackend)]


def test_register_handles_manifest_with_no_providers_key() -> None:
    # Some early plugins shipped manifests without a `providers` array;
    # the canonical helper should treat that as zero providers, not
    # KeyError.
    plugin = Plugin(
        manifest={"plugin_id": "minimal"},
        backend_name="minimal",
        backend_factory=_DemoBackend,
    )
    calls: list[tuple[str, Any, dict[str, Any]]] = []

    def modern_register(name: str, factory: Any, **kwargs: Any) -> None:
        calls.append((name, factory, kwargs))

    plugin.register(modern_register)
    assert calls == [("minimal", _DemoBackend, {"providers": []})]


def test_plugin_is_frozen_dataclass() -> None:
    plugin = Plugin(
        manifest=_manifest_with_providers("demo"),
        backend_name="demo",
        backend_factory=_DemoBackend,
    )
    with pytest.raises(FrozenInstanceError):
        plugin.backend_name = "other"  # type: ignore[misc]


def test_register_hook_replaces_default_register_logic() -> None:
    """Plugins with custom registration (multi-backend COLMAP family,
    RealityScan provider aliases) pass a `register_hook`. When set,
    `register()` delegates entirely to the hook, skipping the default
    provider-id loop + TypeError fallback.
    """
    hook_calls: list[Any] = []

    def custom_hook(register_backend: Callable[..., None]) -> None:
        # Real hook would register multiple (name, factory) pairs, with
        # aliases, etc. Here we just record that the hook ran.
        hook_calls.append(("hook", register_backend))

    default_calls: list[Any] = []

    def fake_register_backend(*args: Any, **kwargs: Any) -> None:
        default_calls.append((args, kwargs))

    plugin = Plugin(
        manifest=_manifest_with_providers("alpha", "beta"),
        backend_name="demo",
        backend_factory=_DemoBackend,
        register_hook=custom_hook,
    )
    plugin.register(fake_register_backend)

    # The hook ran with the registrar passed through.
    assert hook_calls == [("hook", fake_register_backend)]
    # The default register-each-provider loop did NOT run.
    assert default_calls == []


def test_register_hook_defaults_to_none_keeping_default_loop() -> None:
    """Existing call sites that omit register_hook keep the default
    behavior unchanged. Regression guard for the migrated baseline
    plugins that don't need the hook.
    """
    plugin = Plugin(
        manifest=_manifest_with_providers("alpha"),
        backend_name="demo",
        backend_factory=_DemoBackend,
    )
    assert plugin.register_hook is None

    calls: list[tuple[str, Any, dict[str, Any]]] = []

    def modern_register(name: str, factory: Any, **kwargs: Any) -> None:
        calls.append((name, factory, kwargs))

    plugin.register(modern_register)
    assert calls == [("demo", _DemoBackend, {"providers": ["alpha"]})]
