"""Admin endpoints for issuing and revoking API keys.

Mounted at `/v1/admin/...`. These are operator endpoints, not
tenant-scoped API endpoints, and are not protected by sfmapi's tenant
API-key dependency. Production deployments must front this namespace
with an admin-only auth layer.

Two routers live here so the areas can be fenced independently
(SPEC §1.3, lean audit D1/7.1):

* ``router`` — API-key + plugin operator endpoints; part of the
  default OpenAPI contract.
* ``routing_router`` — provider routing-profile endpoints
  (``/v1/admin/routing/*``); Preview tier, excluded from the default
  OpenAPI document unless ``settings.expose_preview_apis`` is set.
  The routes serve identically either way.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.db.models import ApiKey
from app.db.pagination import paginate_sequence
from app.db.session import get_db
from app.schemas.api.plugins import (
    PluginDetailOut,
    PluginDoctorOut,
    PluginEntryPointOut,
    PluginEntryPointPage,
    PluginInstallRequest,
    PluginInstallResponse,
    PluginRegistryItemOut,
    PluginRegistryPage,
    ProviderPriorityRequest,
    RoutingOut,
    RoutingProfileAssignmentRequest,
    RoutingProfileRequest,
    ToolDetectionOut,
)
from app.services import api_key_service, plugin_service

router = APIRouter(prefix="/admin", tags=["admin"])

# Preview tier (SPEC §1.3 [Preview]): mounted unconditionally in
# app.main, but only included in the OpenAPI document when
# ``settings.expose_preview_apis`` is true.
routing_router = APIRouter(prefix="/admin/routing", tags=["admin"])


class IssueKeyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    name: str | None = None


class IssueKeyResponse(BaseModel):
    raw_key: str
    api_key_id: str
    tenant_id: str
    name: str | None


class ApiKeyOut(BaseModel):
    api_key_id: str
    tenant_id: str
    name: str | None
    revoked: bool


@router.post(
    "/api-keys",
    response_model=IssueKeyResponse,
    status_code=status.HTTP_201_CREATED,
)
async def issue(
    body: IssueKeyBody,
    session: AsyncSession = Depends(get_db),
) -> IssueKeyResponse:
    """Mint a fresh API key bound to a tenant.

    Returns the raw key in ``raw_key`` exactly once; only a salted hash
    is persisted, so callers MUST capture the value here. Use the key
    as the ``Bearer`` token in ``Authorization`` against tenant-scoped
    routes once ``auth_mode == "api_key"``.

    WARNING - operator route
    ------------------------
    This route is not tenant-scoped and is not protected by sfmapi's
    tenant API-key dependency. Production deployments MUST front
    ``/v1/admin/...`` with an admin-only auth layer (deploy-time
    master key, mesh-level mTLS, infra-network-only). See ``L2`` in
    ``decisions.md``.
    """
    raw, row = await api_key_service.issue_key(session, tenant_id=body.tenant_id, name=body.name)
    return IssueKeyResponse(
        raw_key=raw,
        api_key_id=row.api_key_id,
        tenant_id=row.tenant_id,
        name=row.name,
    )


@router.delete("/api-keys/{api_key_id}", response_model=ApiKeyOut)
async def revoke(
    api_key_id: str,
    session: AsyncSession = Depends(get_db),
) -> ApiKeyOut:
    """Revoke a previously-issued API key.

    Soft-delete: the row stays for audit, ``revoked_at`` is stamped
    and ``revoked=true`` shipped on the next read. Subsequent auth
    attempts with that key will fail. Idempotent; revoking an
    already-revoked key is a 200 no-op.

    See WARNING on ``POST /v1/admin/api-keys``; this route is an
    operator route and must be protected by deployment infrastructure.
    """
    row = await session.get(ApiKey, api_key_id)
    if row is None:
        raise NotFoundError(f"ApiKey {api_key_id} not found")
    if row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
        await session.flush()
    return ApiKeyOut(
        api_key_id=row.api_key_id,
        tenant_id=row.tenant_id,
        name=row.name,
        revoked=True,
    )


@router.get("/api-keys", response_model=list[ApiKeyOut])
async def list_keys(session: AsyncSession = Depends(get_db)) -> list[ApiKeyOut]:
    """List every API key on file (active + revoked).

    Raw-key material is NEVER returned; use :func:`issue` and capture
    the value at creation time. Ordered by ``created_at`` ascending.

    See WARNING on ``POST /v1/admin/api-keys``; this route is an
    operator route and must be protected by deployment infrastructure.
    """
    rows = (await session.execute(select(ApiKey).order_by(ApiKey.created_at))).scalars().all()
    return [
        ApiKeyOut(
            api_key_id=r.api_key_id,
            tenant_id=r.tenant_id,
            name=r.name,
            revoked=r.revoked_at is not None,
        )
        for r in rows
    ]


@router.get("/plugins", response_model=PluginRegistryPage)
async def list_plugins(
    query: str | None = Query(None),
    page_token: str | None = Query(None),
    page_size: int = Query(50, ge=1, le=500),
) -> PluginRegistryPage:
    """List sfm_hub registry entries and local install state."""
    rows = plugin_service.list_plugins(query)
    if page_token:
        rows = [row for row in rows if str(row["plugin_id"]) > page_token]
    page, next_page_token = paginate_sequence(
        rows,
        page_size=page_size,
        token_for=lambda row: str(row["plugin_id"]),
    )
    return PluginRegistryPage(
        items=[PluginRegistryItemOut.model_validate(row) for row in page],
        next_page_token=next_page_token,
    )


@router.get("/plugins/detect-tools", response_model=ToolDetectionOut)
async def detect_plugin_tools() -> ToolDetectionOut:
    """Detect locally installed external SfM executables."""
    return ToolDetectionOut.model_validate(plugin_service.detect_tools())


@router.get("/plugins/entry-points", response_model=PluginEntryPointPage)
async def list_plugin_entry_points(load: bool = Query(False)) -> PluginEntryPointPage:
    """List installed Python entry points in the sfmapi backend group."""
    return PluginEntryPointPage(
        items=[
            PluginEntryPointOut.model_validate(row)
            for row in plugin_service.list_entry_points(load=load)
        ]
    )


@router.get("/plugins/{plugin_id}", response_model=PluginDetailOut)
async def get_plugin(plugin_id: str) -> PluginDetailOut:
    """Read one sfm_hub plugin manifest and local install state."""
    return PluginDetailOut.model_validate(plugin_service.get_plugin(plugin_id))


@router.post("/plugins/{plugin_id}:install", response_model=PluginInstallResponse)
async def install_plugin(plugin_id: str, body: PluginInstallRequest) -> PluginInstallResponse:
    """Plan or run an operator-scoped plugin install."""
    return PluginInstallResponse.model_validate(
        plugin_service.install_plugin(
            plugin_id,
            method=body.method,
            github_url=body.github_url,
            ref=body.ref,
            package_name=body.package_name,
            dry_run=body.dry_run,
            allow_unsafe_execution=body.allow_unsafe_execution,
            request_id=body.request_id,
            provision_runtime=body.provision_runtime,
            force=body.force,
        )
    )


@router.post("/plugins/{plugin_id}:enable", response_model=PluginDetailOut)
async def enable_plugin(plugin_id: str) -> PluginDetailOut:
    """Enable an installed plugin for provider discovery."""
    return PluginDetailOut.model_validate(plugin_service.enable_plugin(plugin_id))


@router.post("/plugins/{plugin_id}:disable", response_model=PluginDetailOut)
async def disable_plugin(plugin_id: str) -> PluginDetailOut:
    """Disable a plugin without uninstalling its Python package."""
    return PluginDetailOut.model_validate(plugin_service.disable_plugin(plugin_id))


@router.post("/plugins/{plugin_id}:doctor", response_model=PluginDoctorOut)
async def doctor_plugin(plugin_id: str) -> PluginDoctorOut:
    """Run local operator diagnostics for one plugin."""
    return PluginDoctorOut.model_validate(plugin_service.doctor_plugin(plugin_id))


@routing_router.post("/profiles", response_model=RoutingOut)
async def create_routing_profile(body: RoutingProfileRequest) -> RoutingOut:
    """Create or replace a named provider routing profile."""
    return RoutingOut.model_validate(plugin_service.create_profile(body.name, body.routes))


@routing_router.post("/default", response_model=RoutingOut)
async def set_default_routing_profile(body: RoutingProfileAssignmentRequest) -> RoutingOut:
    """Set the default provider routing profile for this sfmapi process."""
    return RoutingOut.model_validate(plugin_service.use_default_profile(body.profile))


@routing_router.post("/provider-priority", response_model=RoutingOut)
async def set_provider_priority(body: ProviderPriorityRequest) -> RoutingOut:
    """Set fallback provider order for unpinned routed stages."""
    return RoutingOut.model_validate(plugin_service.use_provider_priority(body.providers))


@routing_router.post("/projects/{project_id}", response_model=RoutingOut)
async def set_project_routing_profile(
    project_id: str,
    body: RoutingProfileAssignmentRequest,
) -> RoutingOut:
    """Assign a provider routing profile to one project id."""
    return RoutingOut.model_validate(
        plugin_service.assign_project_profile(project_id, body.profile)
    )


@routing_router.post("/workspaces", response_model=RoutingOut)
async def set_workspace_routing_profile(body: RoutingProfileAssignmentRequest) -> RoutingOut:
    """Assign a provider routing profile to the current workspace root."""
    from app.core.config import get_settings

    return RoutingOut.model_validate(
        plugin_service.assign_workspace_profile(str(get_settings().workspace_root), body.profile)
    )
