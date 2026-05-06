"""Backend registry + ``get_backend()`` resolver.

sfmapi ships **no concrete SfM backend**. The wire surface
(REST routes, request/response schemas, error envelope, capability
discovery) is engine-independent; backend implementations live in
separate repositories. To run a real workload, install one such
implementation and register it.

Worker code never imports a specific backend module — it calls
:func:`get_backend` and uses the returned :class:`SfmBackend`. Which
backend is returned is controlled by the ``SFMAPI_BACKEND``
environment variable (or the explicit ``name`` arg).

Adding a backend (separate package or app-startup hook):

.. code-block:: python

    from app.adapters.registry import register_backend

    class MyBackend:
        name = "my_backend"
        ...  # implement SfmBackend

    register_backend("my_backend", MyBackend)

Then set ``SFMAPI_BACKEND=my_backend`` and the worker picks it up
without any other change.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.adapters.backend import SfmBackend


_REGISTRY: dict[str, Callable[[], SfmBackend]] = {}


def register_backend(name: str, factory: Callable[[], SfmBackend]) -> None:
    """Register a backend factory. Re-registering an existing name
    overwrites it (useful for tests)."""
    _REGISTRY[name] = factory


def list_backends() -> list[str]:
    return sorted(_REGISTRY)


def get_backend(name: str | None = None) -> SfmBackend:
    """Resolve and instantiate the configured backend.

    ``name`` overrides the env var when set (mostly for tests).
    Raises :class:`KeyError` if no backend is registered under the
    resolved name — sfmapi ships no default implementation, so the
    caller (or test fixture, or app-startup hook) must register one
    before workers can run.
    """
    chosen = name or os.environ.get("SFMAPI_BACKEND")
    if not chosen:
        raise KeyError(
            "no SfmBackend selected: set SFMAPI_BACKEND or pass `name=` "
            f"explicitly. Registered backends: {list_backends()}"
        )
    if chosen not in _REGISTRY:
        raise KeyError(
            f"unknown SfmBackend {chosen!r}; registered: {list_backends()}. "
            "Install + register a backend implementation in app startup."
        )
    return _REGISTRY[chosen]()


__all__ = ["get_backend", "list_backends", "register_backend"]
