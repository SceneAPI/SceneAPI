"""Backend discovery and extension actions."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from sfmapi.server.api.v1._helpers import accepted_response
from sfmapi.server.core.tenancy import current_tenant
from sfmapi.server.db.pagination import paginate_sequence
from sfmapi.server.db.session import get_db
from sfmapi.server.schemas.api.backend_actions import (
    BackendActionListPage,
    BackendActionOut,
    BackendActionRunRequest,
    BackendActionValidateRequest,
    BackendActionValidateResponse,
    BackendArtifactContractListPage,
    BackendArtifactContractOut,
    BackendConfigSchemaListPage,
    BackendConfigSchemaOut,
    BackendOut,
)
from sfmapi.server.schemas.api.jobs import JobAcceptedResponse
from sfmapi.server.schemas.api.plugins import ProviderOut, ProviderPage, RoutingOut
from sfmapi.server.services import backend_action_service, plugin_service, project_service

router = APIRouter(prefix="/backend", tags=["backend"])

_PROVIDER_PAGE_TOKEN_SEPARATOR = "|"


def _provider_page_key(row: dict[str, object]) -> tuple[str, str]:
    return (str(row["provider_id"]), str(row["plugin_id"]))


def _encode_provider_page_token(row: dict[str, object]) -> str:
    provider_id, plugin_id = _provider_page_key(row)
    return f"{provider_id}{_PROVIDER_PAGE_TOKEN_SEPARATOR}{plugin_id}"


def _decode_provider_page_token(token: str) -> tuple[str, str]:
    if _PROVIDER_PAGE_TOKEN_SEPARATOR not in token:
        return (token, "")
    provider_id, plugin_id = token.split(_PROVIDER_PAGE_TOKEN_SEPARATOR, 1)
    return (provider_id, plugin_id)


@router.get("", response_model=BackendOut)
async def get_backend(
    provider: str | None = Query(
        None,
        description="Optional provider id to inspect instead of the process default backend.",
    ),
) -> BackendOut:
    """Read the active backend identity and extension-action links."""
    return BackendOut.model_validate(backend_action_service.backend_summary(provider=provider))


@router.get("/actions", response_model=BackendActionListPage)
async def list_actions(
    page_token: str | None = Query(None),
    page_size: int = Query(50, ge=1, le=500),
    include_schemas: bool = Query(
        False,
        description="Include each action's input/output schema in the list response.",
    ),
    provider: str | None = Query(None, description="Optional provider id to inspect."),
) -> BackendActionListPage:
    """List backend-native extension actions.

    This is the generic discovery layer for COLMAP commands and future
    backend-specific tools. Portable sfmapi features still belong in
    ``GET /v1/capabilities``; this catalog is intentionally namespaced
    and backend-specific.
    """
    rows, next_page_token = backend_action_service.list_actions(
        page_size=page_size,
        page_token=page_token,
        include_schemas=include_schemas,
        provider=provider,
    )
    return BackendActionListPage(
        items=[BackendActionOut.model_validate(row) for row in rows],
        next_page_token=next_page_token,
    )


@router.get("/config-schemas", response_model=BackendConfigSchemaListPage)
async def list_config_schemas(
    page_token: str | None = Query(None),
    page_size: int = Query(50, ge=1, le=500),
    include_schemas: bool = Query(
        True,
        description="Include JSON Schemas for each backend_options object.",
    ),
    provider: str | None = Query(None, description="Optional provider id to inspect."),
) -> BackendConfigSchemaListPage:
    """List backend-specific option schemas for portable sfmapi stages.

    Clients use this catalog to discover which keys are valid inside a
    stage spec's ``backend_options`` object. The top-level stage spec
    remains the portable sfmapi contract.
    """
    rows, next_page_token = backend_action_service.list_config_schemas(
        page_size=page_size,
        page_token=page_token,
        include_schemas=include_schemas,
        provider=provider,
    )
    return BackendConfigSchemaListPage(
        items=[BackendConfigSchemaOut.model_validate(row) for row in rows],
        next_page_token=next_page_token,
    )


@router.get("/artifact-contracts", response_model=BackendArtifactContractListPage)
async def list_artifact_contracts(
    page_token: str | None = Query(None),
    page_size: int = Query(50, ge=1, le=500),
    provider: str | None = Query(None, description="Optional provider id to inspect."),
) -> BackendArtifactContractListPage:
    """List artifact kinds accepted and emitted by backend portable stages."""
    rows, next_page_token = backend_action_service.list_artifact_contracts(
        page_size=page_size,
        page_token=page_token,
        provider=provider,
    )
    return BackendArtifactContractListPage(
        items=[BackendArtifactContractOut.model_validate(row) for row in rows],
        next_page_token=next_page_token,
    )


@router.post(
    "/actions/{action_id}:validate",
    response_model=BackendActionValidateResponse,
)
async def validate_action(
    action_id: str,
    body: BackendActionValidateRequest,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> BackendActionValidateResponse:
    """Validate backend action inputs without creating a job."""
    if body.project_id is not None:
        await project_service.get_project(
            session,
            tenant_id=tenant_id,
            project_id=body.project_id,
        )
    return BackendActionValidateResponse.model_validate(
        backend_action_service.validate_action(
            action_id,
            body.inputs,
            provider=body.provider,
            project_id=body.project_id,
        )
    )


@router.post(
    "/actions/{action_id}:run",
    response_model=JobAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def run_action(
    action_id: str,
    body: BackendActionRunRequest,
    tenant_id: str = Depends(current_tenant),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Submit a backend-native action as a normal sfmapi job.

    All execution goes through the existing job/task path, so clients
    use ``GET /v1/jobs/{job_id}``, ``/progress``, cancellation, and SSE
    exactly as they do for standard SfM workflows.
    """
    job_id, tasks, backend, provider = await backend_action_service.submit_action(
        session,
        tenant_id=tenant_id,
        project_id=body.project_id,
        action_id=action_id,
        inputs=body.inputs,
        provider=body.provider,
    )
    return accepted_response(
        JobAcceptedResponse(
            job_id=job_id,
            task_ids=[task.task_id for task in tasks],
            project_id=body.project_id,
            action_id=action_id,
            backend=backend,
            provider=provider,
        )
    )


@router.get("/actions/{action_id}", response_model=BackendActionOut)
async def get_action(
    action_id: str,
    provider: str | None = Query(None, description="Optional provider id to inspect."),
) -> BackendActionOut:
    """Read one backend action descriptor including schemas."""
    return BackendActionOut.model_validate(
        backend_action_service.get_action(action_id, provider=provider)
    )


@router.get("/config-schemas/{config_id}", response_model=BackendConfigSchemaOut)
async def get_config_schema(
    config_id: str,
    provider: str | None = Query(None, description="Optional provider id to inspect."),
) -> BackendConfigSchemaOut:
    """Read one backend-specific option schema."""
    return BackendConfigSchemaOut.model_validate(
        backend_action_service.get_config_schema(config_id, provider=provider)
    )


@router.get("/artifact-contracts/{contract_id}", response_model=BackendArtifactContractOut)
async def get_artifact_contract(
    contract_id: str,
    provider: str | None = Query(None, description="Optional provider id to inspect."),
) -> BackendArtifactContractOut:
    """Read one backend artifact input/output contract."""
    return BackendArtifactContractOut.model_validate(
        backend_action_service.get_artifact_contract(contract_id, provider=provider)
    )


@router.get("/providers", response_model=ProviderPage)
async def list_providers(
    page_token: str | None = Query(None),
    page_size: int = Query(50, ge=1, le=500),
) -> ProviderPage:
    """List enabled providers discovered from installed sfm_hub plugins."""
    rows = sorted(plugin_service.list_providers(), key=_provider_page_key)
    total = len(rows)
    if page_token:
        after = _decode_provider_page_token(page_token)
        rows = [row for row in rows if _provider_page_key(row) > after]
    page, next_page_token = paginate_sequence(
        rows,
        page_size=page_size,
        token_for=_encode_provider_page_token,
    )
    return ProviderPage(
        items=[ProviderOut.model_validate(row) for row in page],
        next_page_token=next_page_token,
        total=total,
    )


@router.get("/routing", response_model=RoutingOut)
async def get_routing() -> RoutingOut:
    """Read provider priority and named routing-profile state."""
    return RoutingOut.model_validate(plugin_service.routing_state())
