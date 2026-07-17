"""Tenant scaffolding.

v0: `current_tenant()` returns the configured `default_tenant` regardless of
request. The dependency exists from day 1 so that every router signature
already takes `tenant_id`; in Phase 5 we swap the implementation to read
from an API key without touching any route.
"""

from __future__ import annotations

from contextvars import ContextVar

from fastapi import Request

from sfmapi.server.core.config import get_settings
from sfmapi.server.core.errors import TenantViolationError

_current_tenant_var: ContextVar[str | None] = ContextVar("sfmapi_current_tenant", default=None)


async def current_tenant(request: Request) -> str:
    settings = get_settings()
    if settings.auth_mode == "none":
        tenant = settings.default_tenant
    else:
        key = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if not key:
            raise TenantViolationError("Missing Authorization header")
        from sfmapi.server.db.session import get_session_factory
        from sfmapi.server.services import api_key_service

        factory = get_session_factory()
        async with factory() as session:
            tenant = await api_key_service.resolve_tenant(session, key=key)
    _current_tenant_var.set(tenant)
    return tenant


def get_current_tenant() -> str:
    t = _current_tenant_var.get()
    if t is None:
        raise TenantViolationError("No tenant set in this context")
    return t


def set_current_tenant_for_test(tenant_id: str) -> None:
    _current_tenant_var.set(tenant_id)


def clear_current_tenant() -> None:
    _current_tenant_var.set(None)
