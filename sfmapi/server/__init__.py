"""sfmapi.server - the FastAPI service implementation for sfmapi.

Internal server tree (formerly the top-level ``app`` package; a
deprecated ``app`` alias shim remains for one release). Plugins and
embedders should use the public facades — :mod:`sfmapi.runtime`,
:mod:`sfmapi.backends`, :mod:`sfmapi.errors`, :mod:`sfmapi.testing`,
:mod:`sfmapi.plugin_service` — rather than importing this package.

Backend-agnostic: any SfM engine or native tool wrapper implementing
the appropriate protocol in :mod:`sfmapi.backends` can power the
wire surface. This package ships **no concrete backend**;
implementations (pycolmap, OpenSfM, hloc, vendor CLIs, custom forks)
live in separate repositories and register themselves at app startup
via :func:`sfmapi.runtime.register_backend`. A no-op
:class:`sfmapi.server.adapters.stub_backend.StubBackend` is bundled for tests
and the ``SFMAPI_EPHEMERAL=true`` demo runtime.
"""

__version__ = "0.0.2"
