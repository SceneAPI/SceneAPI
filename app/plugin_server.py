"""Reusable plugin HTTP service adapter -- the server side of
``sfmapi-plugin-http-v1``.

A ``container_service`` plugin runs ONE backend object and exposes it over HTTP
so the sfmapi server can drive it across a versioned protocol (see
``docs/guides/container_plugin_runtime_checklist.md``). This module builds that
server: a plugin packages its :class:`~app.adapters.backend.Backend` + a task
executor, calls :func:`build_plugin_server`, and runs the returned ASGI app.

The endpoints satisfy the contracts the sfmapi side already speaks:

* ``GET  /healthz``             -- liveness (``sfm_hub.doctor`` probes this).
* ``GET  /version``            -- protocol + provenance (doctor's protocol-
  contract check reads ``protocol`` / ``protocol_version``).
* ``GET  /capabilities``       -- the backend capability set.
* ``GET  /actions``            -- the backend's extension actions.
* ``GET  /datatypes``          -- plugin-declared Data Type extensions.
* ``GET  /processors``         -- plugin-declared Processor extensions.
* ``GET  /pipelines``          -- plugin-declared Pipeline extensions.
* ``POST /actions/{id}:validate`` -- validate an action's params.
* ``POST /execute``            -- run one task (the radiance/stage worker POSTs
  ``{protocol, task_kind, capability, tenant_id, job_id, task_id, provider,
  inputs, spec}`` here and reads ``{protocol, ...result}`` back).

Compatibility is major-version based (:func:`protocol_compatible`).
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from typing import Any, Protocol

PROTOCOL = "sfmapi-plugin-http-v1"
PROTOCOL_VERSION = "1.1"


def capabilities_hash(capabilities) -> str:
    """Stable hash of a capability set -- lets the server detect a backend whose
    advertised features changed without re-probing every action."""
    joined = "\n".join(sorted(str(c) for c in capabilities))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def protocol_compatible(client_protocol_version: str) -> bool:
    """Major-version compatibility for ``sfmapi-plugin-http-v1``: a client and
    server are compatible iff they share the protocol major version, so a future
    ``...-http-v2`` (or a 2.x version string) is correctly rejected as
    incompatible while 1.x variants interoperate."""
    try:
        return client_protocol_version.split(".", 1)[0] == PROTOCOL_VERSION.split(".", 1)[0]
    except Exception:
        return False


class TaskExecutor(Protocol):
    """The task runner a plugin supplies: execute one task, return its result
    payload (serialized back to the sfmapi worker as the task outputs)."""

    def __call__(
        self,
        *,
        task_kind: str,
        capability: str,
        inputs: dict[str, Any],
        spec: dict[str, Any],
        tenant_id: str,
        job_id: str,
        task_id: str,
        provider: str,
    ) -> dict[str, Any]: ...


class ManifestBackend:
    """Adapt a manifest-only plugin to the surface :func:`build_plugin_server`
    reads.

    Container-service plugins (the radiance trainers, for example) publish a
    static plugin manifest and run their engine out of process -- they have no
    in-process :class:`~app.adapters.backend.Backend` object to serve. This
    adapter exposes the manifest's capability set (top-level ``capabilities``
    plus every ``providers[].capabilities``) so the kit's capability
    validation and catalog endpoints work unchanged; the extension catalogs
    (datatypes / processors / pipelines) stay empty.
    """

    def __init__(
        self,
        manifest: Mapping[str, Any],
        *,
        version: str = "",
        vendor: str | None = None,
    ) -> None:
        self._manifest = dict(manifest)
        self.name = str(self._manifest.get("plugin_id", ""))
        self.version = version
        self.vendor = str(self._manifest.get("display_name", "")) if vendor is None else vendor

    def capabilities(self) -> list[str]:
        capabilities = {str(c) for c in self._manifest.get("capabilities") or []}
        for provider in self._manifest.get("providers") or []:
            if isinstance(provider, Mapping):
                capabilities.update(str(c) for c in provider.get("capabilities") or [])
        return sorted(capabilities)


def build_plugin_server(
    backend: Any,
    *,
    plugin_id: str,
    package_version: str,
    executor: TaskExecutor,
    runtime_info: Callable[[], Mapping[str, Any]] | None = None,
):
    """Build the ASGI app serving ``backend`` over ``sfmapi-plugin-http-v1``."""
    from fastapi import Body, FastAPI
    from fastapi.responses import JSONResponse

    app = FastAPI(title=f"sfmapi plugin {plugin_id}", docs_url=None, redoc_url=None)
    caps = sorted(str(c) for c in backend.capabilities())

    def _optional_catalog(method_name: str) -> list[Any]:
        method = getattr(backend, method_name, None)
        if method is None:
            return []
        rows = method() if callable(method) else method
        if rows is None:
            return []
        return list(rows)

    def _catalog_schema_version() -> int:
        version = getattr(backend, "catalog_schema_version", 1)
        if callable(version):
            version = version()
        return int(version)

    def _extension_catalog() -> Any:
        """Validate the backend's live declarations before serving them.

        The capability vocabulary is closed (SFMAPI-SPEC.md §6: "The public
        capability surface is closed until plugin-qualified capability ids
        are versioned and client-gated"); a backend advertising ids outside
        ``app.core.capabilities.ALL_KNOWN`` is misconfigured, and every
        catalog-backed endpoint (``/healthz``, ``/capabilities``, ...) must
        surface a server error rather than silently serve garbage.
        """
        from app.core.capabilities import ALL_KNOWN
        from sfm_hub.models import PluginBackendCatalog

        unknown = sorted(set(caps) - ALL_KNOWN)
        if unknown:
            raise ValueError(
                "backend advertises capability ids outside the canonical "
                f"vocabulary (app.core.capabilities.ALL_KNOWN): {', '.join(unknown)}"
            )
        return PluginBackendCatalog.model_validate(
            {
                "schema_version": _catalog_schema_version(),
                "plugin_id": plugin_id,
                "capabilities": caps,
                "datatypes": _optional_catalog("datatypes"),
                "processors": _optional_catalog("processors"),
                "processor_extensions": _optional_catalog("processor_extensions"),
                "pipelines": _optional_catalog("pipelines"),
            }
        )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        _extension_catalog()
        return {"status": "ok"}

    @app.get("/version")
    async def version() -> dict[str, Any]:
        body: dict[str, Any] = {
            "protocol": PROTOCOL,
            "protocol_version": PROTOCOL_VERSION,
            "plugin_id": plugin_id,
            "package_version": package_version,
            "backend_version": str(getattr(backend, "version", "")),
            "capabilities_hash": capabilities_hash(caps),
        }
        if runtime_info is not None:
            body["runtime"] = dict(runtime_info())
        return body

    @app.get("/capabilities")
    async def capabilities() -> dict[str, Any]:
        catalog = _extension_catalog()
        return {
            "schema_version": 1,
            "backend": {
                "name": str(getattr(backend, "name", plugin_id)),
                "version": str(getattr(backend, "version", "")),
                "vendor": str(getattr(backend, "vendor", "")),
            },
            "features": catalog.capabilities,
        }

    @app.get("/actions")
    async def actions() -> dict[str, Any]:
        try:
            from app.adapters.backend_actions import list_backend_actions

            rows = list_backend_actions(backend, include_schemas=False)
        except Exception:
            rows = []
        return {"actions": rows}

    @app.get("/datatypes")
    async def datatypes() -> dict[str, Any]:
        catalog = _extension_catalog()

        return {
            "schema_version": catalog.schema_version,
            "plugin_id": plugin_id,
            "datatypes": [
                row.model_dump(mode="json", exclude_none=True) for row in catalog.datatypes
            ],
        }

    @app.get("/processors")
    async def processors() -> dict[str, Any]:
        catalog = _extension_catalog()

        return {
            "schema_version": catalog.schema_version,
            "plugin_id": plugin_id,
            "processors": [
                row.model_dump(mode="json", exclude_none=True) for row in catalog.processors
            ],
            "processor_extensions": [
                row.model_dump(mode="json", exclude_none=True)
                for row in catalog.processor_extensions
            ],
        }

    @app.get("/pipelines")
    async def pipelines() -> dict[str, Any]:
        catalog = _extension_catalog()

        return {
            "schema_version": catalog.schema_version,
            "plugin_id": plugin_id,
            "pipelines": [
                row.model_dump(mode="json", exclude_none=True) for row in catalog.pipelines
            ],
        }

    @app.post("/actions/{action_id}:validate")
    async def validate_action(action_id: str, params: dict[str, Any] = Body(default={})):
        try:
            from app.adapters.backend_actions import validate_backend_action

            result = validate_backend_action(action_id, params or {}, backend)
        except Exception as exc:  # action surface optional
            return JSONResponse({"valid": False, "errors": [str(exc)]}, status_code=200)
        return {
            "valid": bool(result.get("valid")),
            "errors": list(result.get("errors") or []),
            "normalized_inputs": dict(result.get("normalized_inputs") or {}),
        }

    @app.post("/execute")
    async def execute(payload: dict[str, Any] = Body(...)):
        if payload.get("protocol") != PROTOCOL:
            return JSONResponse(
                {
                    "error": "protocol_mismatch",
                    "expected": PROTOCOL,
                    "got": payload.get("protocol"),
                },
                status_code=400,
            )
        if not protocol_compatible(str(payload.get("protocol_version", ""))):
            return JSONResponse(
                {
                    "error": "protocol_version_mismatch",
                    "expected": PROTOCOL_VERSION,
                    "got": payload.get("protocol_version"),
                },
                status_code=400,
            )
        if payload.get("stage") == "backend_action":
            try:
                from app.adapters import backend_actions

                result = backend_actions.run_backend_action(
                    str(payload["action_id"]),
                    dict(payload.get("inputs") or {}),
                    backend=backend,
                )
            except KeyError as exc:
                return JSONResponse(
                    {"error": "missing_field", "field": str(exc).strip("'")},
                    status_code=400,
                )
            if not isinstance(result, dict):
                result = {"result": result}
            return {
                "protocol": PROTOCOL,
                "status": "succeeded",
                "outputs": result,
            }
        try:
            result = executor(
                task_kind=str(payload["task_kind"]),
                capability=str(payload.get("capability", "")),
                inputs=dict(payload.get("inputs") or {}),
                spec=dict(payload.get("spec") or {}),
                tenant_id=str(payload.get("tenant_id", "")),
                job_id=str(payload.get("job_id", "")),
                task_id=str(payload.get("task_id", "")),
                provider=str(payload.get("provider", "")),
            )
        except KeyError as exc:
            return JSONResponse(
                {"error": "missing_field", "field": str(exc).strip("'")},
                status_code=400,
            )
        if not isinstance(result, dict):
            result = {"result": result}
        return {"protocol": PROTOCOL, **result}

    return app


__all__ = [
    "PROTOCOL",
    "PROTOCOL_VERSION",
    "ManifestBackend",
    "TaskExecutor",
    "build_plugin_server",
    "capabilities_hash",
    "protocol_compatible",
]
