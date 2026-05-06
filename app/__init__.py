"""sfmapi — generic HTTP/REST API for Structure-from-Motion tasks.

Backend-agnostic: any SfM engine implementing
:class:`app.adapters.backend.SfmBackend` can power the wire surface.
The reference implementation wires :class:`ColmapModBackend` (pycolmap
fork), but consumers swap that out via
``app.adapters.registry.register_backend``.
"""

__version__ = "0.0.1"
