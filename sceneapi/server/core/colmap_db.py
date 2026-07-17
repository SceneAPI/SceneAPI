"""Deprecated shim — the contract moved to :mod:`sceneapi.contracts.colmap_db`.

The COLMAP scene-database schema is a deliberate public data contract
consumed outside this repo (e.g. ``sfmapi-cpp/tools/gen_contracts.py``
imports it from ``sceneapi.server.core``), so it now lives in the public ``sfmapi``
package. This module re-exports the full surface so existing
``sceneapi.server.core.colmap_db`` imports keep working for one release; new code
must import :mod:`sceneapi.contracts.colmap_db`.
"""

from __future__ import annotations

import warnings

from sceneapi.contracts.colmap_db import *  # noqa: F403

# Public names outside the contract's __all__ (version components) plus
# the module-internal column helper, so the shim surface is complete.
from sceneapi.contracts.colmap_db import (  # noqa: F401
    DATABASE_VERSION_MAJOR,
    DATABASE_VERSION_MINOR,
    DATABASE_VERSION_PATCH,
    _col,
)

warnings.warn(
    "sceneapi.server.core.colmap_db has moved to sceneapi.contracts.colmap_db; "
    "this compatibility shim will be removed in a future release",
    DeprecationWarning,
    stacklevel=2,
)
