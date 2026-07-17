"""Re-export shim: the COLMAP scene-database schema contract now lives in
the :mod:`sceneapi_io.colmap_db` contract package.

The contract moved into ``sceneapi-io`` (the leaf I/O contract package)
so the sceneapi core, the backend packages, and the generated SDKs all
share one authoritative definition. This module preserves the historic
``sceneapi.contracts.colmap_db`` import path (and, through it, the
deprecated ``sceneapi.server.core.colmap_db`` re-shim) unchanged.
"""

from __future__ import annotations

from sceneapi_io.colmap_db import *  # noqa: F403

# Public names outside the contract's ``__all__`` (the version components)
# plus the module-internal column helper, so downstream shims that import
# them by name (e.g. ``sceneapi.server.core.colmap_db``) keep working.
from sceneapi_io.colmap_db import (  # noqa: F401
    DATABASE_VERSION_MAJOR,
    DATABASE_VERSION_MINOR,
    DATABASE_VERSION_PATCH,
    __all__,
    _col,
)
