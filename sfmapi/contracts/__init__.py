"""Public data-format contracts owned by sfmapi.

Modules here declare cross-tier data standards (schemas, encodings,
registries) as plain data + tiny helpers, with no server or engine
imports. Plugins, exporters, and the C++ port consume them from this
public package; the server's internal ``app`` package is not a public
import surface.

Current contracts:

- :mod:`sfmapi.contracts.colmap_db` — the extended COLMAP scene-database
  schema (moved from ``sfmapi.server.core.colmap_db``, which remains as a
  deprecated re-export shim for one release).
"""
