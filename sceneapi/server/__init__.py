"""sceneapi.server - the FastAPI service implementation for sceneapi.

Internal server tree (formerly ``sfmapi.server``; a deprecated
``sfmapi`` alias shim over :mod:`sceneapi` remains for one release,
removed in 0.2.0). Plugins and
embedders should use the public facades — :mod:`sceneapi.runtime`,
:mod:`sceneapi.backends`, :mod:`sceneapi.errors`, :mod:`sceneapi.testing`,
:mod:`sceneapi.plugin_service` — rather than importing this package.

Backend-agnostic: any SfM engine or native tool wrapper implementing
the appropriate protocol in :mod:`sceneapi.backends` can power the
wire surface. This package ships **no concrete backend**;
implementations (pycolmap, OpenSfM, hloc, vendor CLIs, custom forks)
live in separate repositories and register themselves at app startup
via :func:`sceneapi.runtime.register_backend`. A no-op
:class:`sceneapi.server.adapters.stub_backend.StubBackend` is bundled for tests
and the ``SCENEAPI_EPHEMERAL=true`` demo runtime.
"""

__version__ = "0.1.0"
