"""Container-service plugin kit -- the supported surface for plugins that
serve ``sfmapi-plugin-http-v1`` over HTTP.

A ``container_service`` plugin exposes one backend (or one manifest-declared
provider) so the sfmapi server can drive it across the versioned protocol.
Build the ASGI app with :func:`build_plugin_server` instead of hand-rolling a
FastAPI app + protocol module:

* live in-process backend::

    app = build_plugin_server(
        backend, plugin_id="...", package_version=..., executor=...
    )

* manifest-only plugin (engine runs out of process)::

    app = build_plugin_server(
        ManifestBackend(MANIFEST, version=__version__),
        plugin_id=MANIFEST["plugin_id"],
        package_version=__version__,
        executor=...,
        runtime_info=...,  # optional /version diagnostics
    )

The returned app serves ``/healthz``, ``/version``, ``/capabilities``,
``/actions``, ``/datatypes``, ``/processors``, ``/pipelines``,
``/actions/{id}:validate``, and ``/execute`` at :data:`PROTOCOL_VERSION` --
the contract ``sfm_hub.doctor`` and the sfmapi workers already speak.
Everything here is a re-export of :mod:`sceneapi.server.plugin_server`; like
:mod:`sceneapi.runtime`, this module is the public ``sfmapi.*`` surface plugins
should import.
"""

from __future__ import annotations

from sceneapi.server.plugin_server import (
    PROTOCOL,
    PROTOCOL_VERSION,
    ManifestBackend,
    TaskExecutor,
    build_plugin_server,
    capabilities_hash,
    protocol_compatible,
)

__all__ = [
    "PROTOCOL",
    "PROTOCOL_VERSION",
    "ManifestBackend",
    "TaskExecutor",
    "build_plugin_server",
    "capabilities_hash",
    "protocol_compatible",
]
