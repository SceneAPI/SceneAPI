"""Service helpers for backend-native actions."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters import backend_actions, backend_artifacts, backend_config
from app.adapters.registry import get_backend
from app.core.config import get_settings
from app.core.errors import ValidationError
from app.core.ids import new_id
from app.orchestrator.dag import TaskNode, hash_inputs, hash_params
from app.orchestrator.scheduler import submit_job_dag
from app.services import project_service


def backend_summary() -> dict[str, Any]:
    backend = get_backend()
    actions = backend_actions.list_backend_actions(backend, include_schemas=False)
    config_schemas = backend_config.list_backend_config_schemas(backend, include_schemas=False)
    artifact_contracts = backend_artifacts.list_backend_artifact_contracts(backend)
    links: dict[str, dict[str, str]] = {
        "self": {"href": "/v1/backend"},
        "actions": {"href": "/v1/backend/actions"},
        "config_schemas": {"href": "/v1/backend/config-schemas"},
        "artifact_contracts": {"href": "/v1/backend/artifact-contracts"},
        "providers": {"href": "/v1/backend/providers"},
        "routing": {"href": "/v1/backend/routing"},
    }
    settings = get_settings()
    if settings.mcp_api_enabled():
        mcp_path = settings.normalized_mcp_mount_path()
        links["mcp"] = {"href": mcp_path}
        links["mcp_status"] = {"href": f"{mcp_path}/status"}
    return {
        "name": str(getattr(backend, "name", "unknown")),
        "version": str(getattr(backend, "version", "")),
        "vendor": str(getattr(backend, "vendor", "")),
        "runtime_versions": dict(backend.runtime_versions()),
        "action_count": len(actions),
        "config_schema_count": len(config_schemas),
        "artifact_contract_count": len(artifact_contracts),
        "_links": links,
    }


def list_actions(
    *,
    page_size: int = 50,
    page_token: str | None = None,
    include_schemas: bool = False,
) -> tuple[list[dict[str, Any]], str | None]:
    actions = backend_actions.list_backend_actions(include_schemas=include_schemas)
    if page_token:
        actions = [action for action in actions if str(action["action_id"]) > page_token]
    rows = actions[: page_size + 1]
    next_page_token = None
    if len(rows) > page_size:
        next_page_token = str(rows[page_size - 1]["action_id"])
        rows = rows[:page_size]
    return rows, next_page_token


def get_action(action_id: str) -> dict[str, Any]:
    return backend_actions.get_backend_action(action_id)


def list_config_schemas(
    *,
    page_size: int = 50,
    page_token: str | None = None,
    include_schemas: bool = True,
) -> tuple[list[dict[str, Any]], str | None]:
    rows = backend_config.list_backend_config_schemas(include_schemas=include_schemas)
    if page_token:
        rows = [row for row in rows if str(row["config_id"]) > page_token]
    page = rows[: page_size + 1]
    next_page_token = None
    if len(page) > page_size:
        next_page_token = str(page[page_size - 1]["config_id"])
        page = page[:page_size]
    return page, next_page_token


def get_config_schema(config_id: str) -> dict[str, Any]:
    return backend_config.get_backend_config_schema(config_id)


def list_artifact_contracts(
    *,
    page_size: int = 50,
    page_token: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    rows = backend_artifacts.list_backend_artifact_contracts()
    if page_token:
        rows = [row for row in rows if str(row["contract_id"]) > page_token]
    page = rows[: page_size + 1]
    next_page_token = None
    if len(page) > page_size:
        next_page_token = str(page[page_size - 1]["contract_id"])
        page = page[:page_size]
    return page, next_page_token


def get_artifact_contract(contract_id: str) -> dict[str, Any]:
    return backend_artifacts.get_backend_artifact_contract(contract_id)


def validate_action(action_id: str, inputs: dict[str, Any]) -> dict[str, Any]:
    return backend_actions.validate_backend_action(action_id, inputs)


async def submit_action(
    session: AsyncSession,
    *,
    tenant_id: str,
    project_id: str,
    action_id: str,
    inputs: dict[str, Any],
) -> tuple[str, list[Any], str]:
    """Submit one backend-native action as an sfmapi job."""
    await project_service.get_project(session, tenant_id=tenant_id, project_id=project_id)
    action = get_action(action_id)
    validation = validate_action(action_id, inputs)
    if not validation.get("valid"):
        detail = "; ".join(str(error.get("message")) for error in validation.get("errors") or [])
        raise ValidationError(detail or f"invalid inputs for backend action {action_id!r}")

    backend = get_backend()
    normalized_inputs = dict(validation.get("normalized_inputs") or inputs or {})
    task_inputs = {
        "action_id": action_id,
        "project_id": project_id,
        # Arbitrary backend actions may have side effects, so avoid the
        # normal task cache unless a future action-specific contract
        # explicitly declares idempotent cache semantics.
        "run_id": new_id(),
    }
    task_spec = {
        "inputs": normalized_inputs,
        "action": {
            "action_id": action["action_id"],
            "backend": action["backend"],
            "side_effects": action["side_effects"],
        },
    }
    node = TaskNode(
        task_id=new_id(),
        kind="backend_action",
        inputs_hash=hash_inputs(task_inputs),
        params_hash=hash_params(task_spec),
        gpu_required=bool(action.get("gpu_required", True)),
        metadata={"inputs": task_inputs, "spec": task_spec},
    )
    job_id, tasks = await submit_job_dag(
        session,
        tenant_id=tenant_id,
        project_id=project_id,
        recipe="backend_action",
        spec={"action_id": action_id, "backend": str(getattr(backend, "name", "unknown"))},
        nodes=[node],
    )
    return job_id, tasks, str(getattr(backend, "name", "unknown"))
