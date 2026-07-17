"""Deprecated compatibility alias: ``app`` -> :mod:`sfmapi.server`.

The server implementation moved from the top-level ``app`` package into
the ``sfmapi`` namespace as ``sfmapi.server`` (lean audit 2026-07, item
7.4 / decision D4). This shim keeps every old import path working for
one release and is **removed in sfmapi 0.1.0**:

* ``import app.main`` / ``from app.core.errors import SfmApiError`` /
  ``importlib.import_module("app.workers.runner")`` all resolve to the
  *same module objects* as their ``sfmapi.server.*`` counterparts, so
  ``isinstance`` / ``except`` / monkeypatching across the two names
  stay coherent.
* ``import app; app.main`` works via eagerly-set attributes.

Plugins and embedders should not import this package (or
``sfmapi.server``) at all — use the public facades: ``sfmapi.runtime``,
``sfmapi.backends``, ``sfmapi.errors``, ``sfmapi.testing``,
``sfmapi.plugin_service``, ``sfmapi.contracts``.
"""

import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import sys
import warnings

from sfmapi.server import __version__

__all__ = ["__version__"]

_REAL_PACKAGE = "sfmapi.server"

warnings.warn(
    "The top-level 'app' package has been renamed to 'sfmapi.server'; "
    "the 'app' compatibility alias is deprecated and will be removed in "
    "sfmapi 0.1.0. Import 'sfmapi.server' (internal) or, for plugins, "
    "the public 'sfmapi.*' facades instead.",
    DeprecationWarning,
    stacklevel=2,
)


class _AliasLoader(importlib.abc.Loader):
    """Loader that returns the already-imported real module unchanged."""

    def __init__(self, real_name: str) -> None:
        self._real_name = real_name
        self._real_spec = None

    def create_module(self, spec):
        module = importlib.import_module(self._real_name)
        self._real_spec = getattr(module, "__spec__", None)
        return module

    def exec_module(self, module) -> None:
        # ``module_from_spec`` stamps the alias spec onto the (shared)
        # real module; restore the canonical one so the module keeps
        # reporting its real identity.
        if self._real_spec is not None:
            module.__spec__ = self._real_spec


class _AliasFinder(importlib.abc.MetaPathFinder):
    """Resolve any ``app.x.y`` import to the ``sfmapi.server.x.y`` module.

    Deeper-than-first-level names (``app.core.errors``, ``app.workers.
    tasks.extract``, ...) are not eagerly aliased below; without this
    finder they would fall through to the path finder and load a
    *duplicate* module from the real package's ``__path__``, breaking
    class identity between the two names.
    """

    def find_spec(self, fullname, path=None, target=None):
        # Never claim "app" itself -- the on-disk shim package handles it.
        if not fullname.startswith("app."):
            return None
        real_name = _REAL_PACKAGE + fullname[len("app") :]
        try:
            real_spec = importlib.util.find_spec(real_name)
        except (ImportError, ValueError):
            return None
        if real_spec is None:
            return None
        # is_package deliberately stays False: a truthy value would make
        # ``module_from_spec`` overwrite the shared real module's
        # ``__path__`` with the spec's empty search-locations list.
        # Submodule resolution still works because ``sys.modules`` holds
        # the real package object (with its real ``__path__``).
        return importlib.machinery.ModuleSpec(fullname, _AliasLoader(real_name))


if not any(isinstance(finder, _AliasFinder) for finder in sys.meta_path):
    sys.meta_path.insert(0, _AliasFinder())

# Eagerly alias every first-level submodule so that
# ``import app; app.main`` and ``sys.modules["app.core"]`` work without
# a further import statement.
_SUBMODULES = (
    "api",
    "core",
    "db",
    "schemas",
    "sources",
    "storage",
    "orchestrator",
    "services",
    "workers",
    "adapters",
    "mcp",
    "main",
    "cli",
    "plugin_server",
    "scaffolding",
)

for _name in _SUBMODULES:
    _module = importlib.import_module(f"{_REAL_PACKAGE}.{_name}")
    sys.modules[f"app.{_name}"] = _module
    globals()[_name] = _module
del _name, _module
