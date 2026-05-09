"""Backend discovery and extension actions."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1._helpers import accepted_response
from app.core.tenancy import current_tenant
from app.db.session import get_db
from app.schemas.api.backend_actions import (
    BackendActionListPage,
    BackendActionOut,
    BackendActionRunRequest,
    BackendActionValidateRequest,
    BackendActionValidateResponse,
    BackendConfigSchemaListPage,
    BackendConfigSchemaOut,
    BackendOut,
)
from app.schemas.api.jobs import JobAcceptedResponse
from app.services import backend_action_service

router = APIRouter(prefix="/backend", tags=["backend"])


@router.get("", response_model=BackendOut)
async def get_backend() -> BackendOut:
    """Read the active backend identity and extension-action links."""
    return BackendOut.model_validate(backend_action_service.backend_summary())


@router.get("/actions", response_model=BackendActionListPage)
async def list_actions(
    page_token: str | None = Query(None),
    page_size: int = Query(50, ge=1, le=500),
    include_schemas: bool = Query(
        False,
        description="Include each action's input/output schema in the list response.",
    ),
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
    )
    return BackendConfigSchemaListPage(
        items=[BackendConfigSchemaOut.model_validate(row) for row in rows],
        next_page_token=next_page_token,
    )


@router.post(
    "/actions/{action_id}:validate",
    response_model=BackendActionValidateResponse,
)
async def validate_action(
    action_id: str,
    body: BackendActionValidateRequest,
) -> BackendActionValidateResponse:
    """Validate backend action inputs without creating a job."""
    return BackendActionValidateResponse.model_validate(
        backend_action_service.validate_action(action_id, body.inputs)
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
    job_id, tasks, backend = await backend_action_service.submit_action(
        session,
        tenant_id=tenant_id,
        project_id=body.project_id,
        action_id=action_id,
        inputs=body.inputs,
    )
    return accepted_response(
        JobAcceptedResponse(
            job_id=job_id,
            task_ids=[task.task_id for task in tasks],
            project_id=body.project_id,
            action_id=action_id,
            backend=backend,
        )
    )


@router.get("/actions/{action_id}", response_model=BackendActionOut)
async def get_action(action_id: str) -> BackendActionOut:
    """Read one backend action descriptor including schemas."""
    return BackendActionOut.model_validate(backend_action_service.get_action(action_id))


@router.get("/config-schemas/{config_id}", response_model=BackendConfigSchemaOut)
async def get_config_schema(config_id: str) -> BackendConfigSchemaOut:
    """Read one backend-specific option schema."""
    return BackendConfigSchemaOut.model_validate(
        backend_action_service.get_config_schema(config_id)
    )
