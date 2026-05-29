# SPDX-License-Identifier: Apache-2.0
# Copyright the sfmapi authors. See LICENSE (Apache-2.0).
"""Backend registry and provider-aware resolver.

sfmapi ships no concrete SfM backend. The wire surface is engine-independent;
backend implementations live in separate packages and register factories at
startup. Worker code never imports a specific backend module. It resolves a
backend by one of two selectors:

- a backend name, normally from ``SFMAPI_BACKEND``;
- a provider id resolved by the sfm_hub routing layer.

Adding a backend package:

.. code-block:: python

    from app.adapters.registry import register_backend

    class MyBackend:
        name = "my_backend"

    register_backend("my_backend", MyBackend, providers=["my_provider"])

Then set ``SFMAPI_BACKEND=my_backend`` for process-wide default execution, or
let sfm_hub route a stage to ``my_provider``.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.adapters.backend import Backend


_REGISTRY: dict[str, Callable[[], Backend]] = {}
_PROVIDER_REGISTRY: dict[str, Callable[[], Backend]] = {}


def register_backend(
    name: str,
    factory: Callable[[], Backend],
    *,
    providers: Sequence[str] | None = None,
) -> None:
    """Register a backend factory.

    Re-registering an existing name overwrites it, which keeps tests and app
    startup hooks simple. ``providers`` registers sfm_hub provider ids as
    aliases for the same factory.
    """

    _REGISTRY[name] = factory
    for provider_id in providers or ():
        register_backend_provider(provider_id, factory)


def register_backend_provider(provider_id: str, factory: Callable[[], Backend]) -> None:
    """Register one sfm_hub provider id to a backend factory.

    If two plugins declare the same ``provider_id`` (e.g. the umbrella
    ``colmap_native`` plugin and the granular ``sfmapi_colmap_cli``
    both claim ``colmap_cli``) the last registration wins — but emit a
    warning so the operator knows the resolution is install-order
    dependent. Pin the explicit plugin you want and uninstall the other,
    or use a routing profile, to make the choice deterministic.
    """
    existing = _PROVIDER_REGISTRY.get(provider_id)
    if existing is not None and existing is not factory:
        from app.core.logging import get_logger

        get_logger("adapters.registry").warning(
            "backend.provider_collision",
            provider_id=provider_id,
            previous=getattr(existing, "__qualname__", str(existing)),
            replacement=getattr(factory, "__qualname__", str(factory)),
            hint=(
                "two plugins declare the same sfm_hub provider id; the second "
                "registration wins. Uninstall one plugin or set a routing "
                "profile to make the choice deterministic."
            ),
        )
    _PROVIDER_REGISTRY[provider_id] = factory


def list_backends() -> list[str]:
    return sorted(_REGISTRY)


def list_backend_providers() -> list[str]:
    return sorted(_PROVIDER_REGISTRY)


def get_backend(name: str | None = None, *, provider: str | None = None) -> Backend:
    """Resolve and instantiate a backend.

    ``provider`` is used by routed stage execution. ``name`` is the legacy
    backend selector and overrides ``SFMAPI_BACKEND`` when supplied.
    """

    if provider:
        if provider in _PROVIDER_REGISTRY:
            return _PROVIDER_REGISTRY[provider]()
        if provider in _REGISTRY:
            return _REGISTRY[provider]()
        raise KeyError(
            f"unknown sfmapi provider {provider!r}; registered providers: "
            f"{list_backend_providers()}; registered backends: {list_backends()}. "
            "Install, enable, and load a backend plugin that declares this provider."
        )

    chosen = name or os.environ.get("SFMAPI_BACKEND")
    if not chosen:
        raise KeyError(
            "no sfmapi backend selected: set SFMAPI_BACKEND or pass `name=` "
            f"explicitly. Registered backends: {list_backends()}; "
            f"registered providers: {list_backend_providers()}"
        )
    if chosen not in _REGISTRY:
        raise KeyError(
            f"unknown sfmapi backend {chosen!r}; registered: {list_backends()}. "
            "Install + register a backend implementation in app startup."
        )
    return _REGISTRY[chosen]()


__all__ = [
    "get_backend",
    "list_backend_providers",
    "list_backends",
    "register_backend",
    "register_backend_provider",
]
