"""Deprecated compatibility alias: ``sfmapi`` -> :mod:`sceneapi`.

The project renamed from ``sfmapi`` to ``sceneapi`` in 0.1.0 (SceneAPI
migration Phase B, ``docs/_internal/sceneapi_migration_proposal.md``).
This shim keeps every old import path working for one release and is
**removed in sceneapi 0.2.0**:

* ``import sfmapi.runtime`` / ``from sfmapi.backends import Plugin`` /
  ``importlib.import_module("sfmapi.server.core.errors")`` all resolve
  to the *same module objects* as their ``sceneapi.*`` counterparts, so
  ``isinstance`` / ``except`` / monkeypatching across the two names
  stay coherent.
* ``import sfmapi; sfmapi.runtime`` works via eagerly-set attributes.

Wire identity is untouched by the rename (SFMAPI-SPEC.md, the
``sfmapi.*.v1`` format ids, ``application/x-sfm-*`` media types, and
the sfmapi.github.io error URIs stay until migration Phase C).

Plugins and embedders should import the public ``sceneapi.*`` facades:
``sceneapi.runtime``, ``sceneapi.backends``, ``sceneapi.errors``,
``sceneapi.testing``, ``sceneapi.plugin_service``, ``sceneapi.contracts``.
"""

import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import sys
import warnings

from sceneapi.server import __version__

__all__ = ["__version__"]

_REAL_PACKAGE = "sceneapi"

warnings.warn(
    "The 'sfmapi' package has been renamed to 'sceneapi'; the 'sfmapi' "
    "compatibility alias is deprecated and will be removed in sceneapi "
    "0.2.0. Import the public 'sceneapi.*' facades (sceneapi.runtime, "
    "sceneapi.backends, ...) instead.",
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
    """Resolve any ``sfmapi.x.y`` import to the ``sceneapi.x.y`` module.

    Deeper-than-first-level names (``sfmapi.server.core.errors``,
    ``sfmapi.server.workers.tasks.extract``, ...) are not eagerly
    aliased below; without this finder they would fall through to the
    path finder and load a *duplicate* module from the real package's
    ``__path__``, breaking class identity between the two names.
    """

    def find_spec(self, fullname, path=None, target=None):
        # Never claim "sfmapi" itself -- the on-disk shim package handles it.
        if not fullname.startswith("sfmapi."):
            return None
        real_name = _REAL_PACKAGE + fullname[len("sfmapi") :]
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
# ``import sfmapi; sfmapi.runtime`` and ``sys.modules["sfmapi.server"]``
# work without a further import statement.
_SUBMODULES = (
    "backends",
    "contracts",
    "errors",
    "plugin_service",
    "runtime",
    "server",
    "testing",
)

for _name in _SUBMODULES:
    _module = importlib.import_module(f"{_REAL_PACKAGE}.{_name}")
    sys.modules[f"sfmapi.{_name}"] = _module
    globals()[_name] = _module
del _name, _module
