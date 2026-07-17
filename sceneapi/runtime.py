"""Runtime hooks for embedding sfmapi with backend plugins."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from fastapi import FastAPI

from sceneapi.server.adapters.backend import Backend
from sceneapi.server.adapters.registry import (
    get_backend,
    list_backend_providers,
    list_backends,
    register_backend,
    register_backend_provider,
)
from sfm_hub.discovery import load_backend_entry_points

BackendFactory = Callable[[], Backend]


class BackendPlugin(Protocol):
    """Plugin object shape accepted by :func:`register_plugin`."""

    def register(self, register_backend: Callable[[str, BackendFactory], None]) -> None: ...


def register_plugin(plugin: BackendPlugin) -> None:
    """Register one plugin object with the process-local backend registry."""

    plugin.register(register_backend)


def load_installed_plugins() -> list[Any]:
    """Load installed backend entry points into the process-local registry."""

    return load_backend_entry_points(register_backend, register_provider=register_backend_provider)


def create_app() -> FastAPI:
    """Create the FastAPI app without side effects at ``sceneapi.runtime`` import time."""

    from sceneapi.server.main import create_app as _create_app

    return _create_app()


__all__ = [
    "BackendFactory",
    "create_app",
    "get_backend",
    "list_backend_providers",
    "list_backends",
    "load_installed_plugins",
    "register_backend",
    "register_backend_provider",
    "register_plugin",
]
