"""sfmapi — generic HTTP/REST API for Structure-from-Motion tasks.

Backend-agnostic: any SfM engine implementing
:class:`app.adapters.backend.SfmBackend` can power the wire surface.
This package ships **no concrete backend** — implementations
(pycolmap, OpenSfM, hloc, custom forks) live in separate
repositories and register themselves at app startup via
:func:`app.adapters.registry.register_backend`. A no-op
:class:`app.adapters.stub_backend.StubBackend` is bundled for tests
and the ``SFMAPI_EPHEMERAL=true`` demo runtime.
"""

__version__ = "0.0.1"
