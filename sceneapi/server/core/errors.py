"""Domain error hierarchy + RFC7807 problem+json mapping."""

from __future__ import annotations

from typing import Any

from sceneapi_io.errors import SceneIoError


class SfmApiError(Exception):
    """Base class for every domain error sfmapi raises.

    Subclasses MUST set ``status_code`` (HTTP) and ``error_type`` (the
    slug used to build ``type: https://sfmapi.github.io/errors/<slug>``
    in the RFC 7807 envelope). The FastAPI exception handler in
    ``sceneapi/server/main.py::sceneapi_error_handler`` turns these into
    ``application/problem+json`` responses; never raise raw
    ``HTTPException`` from services — keep the wire shape consistent.

    Free-form ``extras`` are merged into the envelope as top-level
    keys (``capability`` on 501, ``retry_after`` on 429); see the
    typed surface in :class:`~sceneapi.server.schemas.api.common.ProblemResponse`.
    """

    status_code: int = 500
    error_type: str = "internal"
    title: str = "Internal error"

    def __init__(self, detail: str = "", **extras: Any) -> None:
        super().__init__(detail)
        self.detail = detail or self.title
        self.extras = extras

    def as_problem(self, instance: str | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {
            "type": f"https://sfmapi.github.io/errors/{self.error_type}",
            "title": self.title,
            "status": self.status_code,
            "detail": self.detail,
        }
        if instance:
            out["instance"] = instance
        out.update(self.extras)
        return out


class BadRequestError(SfmApiError):
    """400 — request couldn't be parsed or violates a non-schema invariant.

    Use ``ValidationError`` (422) when the request shape is well-formed
    but a field value is wrong; use this when the request couldn't
    even be interpreted (e.g., malformed Content-Range, conflicting
    query params)."""

    status_code = 400
    error_type = "bad_request"
    title = "Bad request"


class NotFoundError(SfmApiError):
    """404 — the addressed resource doesn't exist for this tenant.

    Routes MUST raise this rather than returning ``None`` so the
    failure shows up as ``application/problem+json``. Tenant scoping
    is implicit: a row that exists under a different tenant looks
    like 404 to the caller (see ``L2`` — multi-tenant boundary).
    """

    status_code = 404
    error_type = "not_found"
    title = "Resource not found"


class ConflictError(SfmApiError):
    """409 — the request conflicts with the current resource state.

    Use for optimistic-concurrency / state-machine violations
    (e.g., finalizing an upload that's already finalized, deleting a
    project with active jobs). Use :class:`ValidationError` (422) when
    the request itself is wrong; use this when the request would be
    valid against a different server state.
    """

    status_code = 409
    error_type = "conflict"
    title = "Conflict"


class ValidationError(SfmApiError):
    """422 — semantic validation failed after parsing succeeded.

    Distinct from FastAPI's :class:`fastapi.RequestValidationError`
    (Pydantic schema-shape failures): this fires from inside services
    when a value is well-typed but rejected on a domain rule
    (cross-field consistency, foreign-key lookup, plausibility). Both
    paths emit the same RFC 7807 ``problem+json`` shape — see ``L19``
    for the structured ``errors[]`` invariant.
    """

    status_code = 422
    error_type = "validation"
    title = "Validation error"


class TenantViolationError(SfmApiError):
    """403 — tenant scoping was bypassed or auth resolution failed.

    Raised by :func:`sceneapi.server.core.tenancy.current_tenant` when the
    Authorization header is missing / unrecognized, and by repository
    helpers that detect a tenant_id mismatch. See ``L2`` in
    ``decisions.md`` (multi-tenant from day 1, ``default`` until auth
    lands). Once real auth ships, 401 (missing) and 403 (rejected)
    will split — for now both surface here.
    """

    status_code = 403
    error_type = "tenant_violation"
    title = "Tenant boundary violation"


class CapabilityUnavailableError(SfmApiError):
    """The requested SfM feature isn't supported by the current backend.

    Returns ``501 Not Implemented`` rather than 5xx because the request
    itself is well-formed and the server is healthy — it just doesn't
    expose the requested capability. The ``capability`` extra carries
    the canonical feature name (see :mod:`sceneapi.server.core.capabilities`) so
    clients can correlate with ``GET /v1/capabilities``.
    """

    status_code = 501
    error_type = "capability_unavailable"
    title = "Capability not available in this deployment"

    def __init__(self, *, capability: str, reason: str = "") -> None:
        detail = reason or f"capability {capability!r} not supported by the current backend"
        super().__init__(detail=detail, capability=capability)


class BackendUnavailableError(CapabilityUnavailableError):
    """The registered backend can't load the engine this request needs.

    Engine-neutral: raised when the backend package itself is missing
    or broken (import failure, absent native dependency), as opposed to
    a healthy backend that simply doesn't implement the capability
    (plain :class:`CapabilityUnavailableError`). Same 501 status and
    RFC 7807 shape as its parent; ``capability`` names the missing
    engine surface. The worker dispatcher catches this base class and
    derives the task's ``error_class`` from the exception type name.
    """

    error_type = "backend_unavailable"
    title = "Backend engine not available in this deployment"

    def __init__(self, reason: str = "", *, capability: str = "backend") -> None:
        super().__init__(capability=capability, reason=reason or self.title)


class PycolmapUnavailableError(BackendUnavailableError):
    """Deprecated alias: the colmap backend can't load pycolmap.

    Kept as a subclass of the engine-neutral
    :class:`BackendUnavailableError` for backwards compatibility — the
    class name is serialized state (tasks persist ``error_class =
    "PycolmapUnavailable"`` and the wire slug is
    ``pycolmap_unavailable``), so it survives until 0.1.0. New code
    should raise / catch :class:`BackendUnavailableError` instead.
    Status code stays 501 — the failure is the same shape (a capability
    the deployment doesn't expose), even though the reason is
    backend-specific.
    """

    error_type = "pycolmap_unavailable"
    title = "pycolmap not available in this deployment"

    def __init__(self, reason: str = "") -> None:
        super().__init__(reason or self.title, capability="pycolmap")


class QuotaExceededError(SfmApiError):
    """429 — caller hit a per-tenant or per-request rate / size cap.

    Currently fires on oneshot request-body caps
    (``oneshot_max_request_bytes``); Phase 5 plumbs in fair-share
    scheduling + per-tenant job quotas through this same error. SDK
    ergonomics shims interpret 429 as retryable when ``retry_after``
    is set.
    """

    status_code = 429
    error_type = "quota_exceeded"
    title = "Quota exceeded"


class StorageError(SceneIoError, SfmApiError):
    """507 Insufficient Storage — the backing store rejected a write.

    Used for blob-store write failures, snapshot rename collisions,
    workspace-out-of-space conditions. Distinct from a generic 500:
    the request itself was valid, the storage layer couldn't hold it.

    Also subclasses :class:`sceneapi_io.errors.SceneIoError` (the base
    the relocated I/O codecs raise) so that codec-level failures and
    ``StorageError`` share one 507 mapping. The MRO is
    ``StorageError -> SceneIoError -> SfmApiError``: Starlette resolves
    the exception handler by walking that MRO, and the ``SceneIoError``
    handler in :mod:`sceneapi.server.main` defers to
    :meth:`SfmApiError.as_problem` for any ``SfmApiError`` instance, so a
    ``StorageError`` renders byte-identically to before.
    """

    status_code = 507
    error_type = "storage"
    title = "Storage error"
