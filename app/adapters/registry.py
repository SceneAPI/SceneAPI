"""Backend registry + ``get_backend()`` resolver.

Worker code never imports a specific backend module. It calls
:func:`get_backend` and uses the returned :class:`SfmBackend`. The
backend chosen is controlled by the ``SFMAPI_BACKEND`` environment
variable (default ``"colmap_mod"``).

To add a new backend:

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
_DEFAULT = "colmap_mod"


def register_backend(name: str, factory: Callable[[], SfmBackend]) -> None:
    """Register a backend factory. Re-registering an existing name
    overwrites it (useful for tests)."""
    _REGISTRY[name] = factory


def list_backends() -> list[str]:
    return sorted(_REGISTRY)


def get_backend(name: str | None = None) -> SfmBackend:
    """Resolve and instantiate the configured backend.

    ``name`` overrides the env var when set (mostly for tests). If no
    factory is registered for the resolved name and the name is the
    default, lazily register the colmap_mod backend so the worker
    boots without an explicit registration call somewhere."""
    chosen = name or os.environ.get("SFMAPI_BACKEND", _DEFAULT)
    if chosen not in _REGISTRY:
        if chosen == _DEFAULT:
            from app.adapters.colmap_backend import ColmapModBackend

            register_backend(_DEFAULT, ColmapModBackend)
        else:
            raise KeyError(f"unknown SfmBackend {chosen!r}; registered: {list_backends()}")
    return _REGISTRY[chosen]()


__all__ = ["get_backend", "list_backends", "register_backend"]
