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
from sfm_hub.routing import ensure_provider_enabled


def _resolve_backend(provider: str | None = None) -> Any:
    try:
        if provider is not None:
            ensure_provider_enabled(provider)
        return get_backend(provider=provider)
    except KeyError as exc:
        raise ValidationError(str(exc)) from exc


def backend_summary(*, provider: str | None = None) -> dict[str, Any]:
    backend = _resolve_backend(provider)
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
    provider: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    backend = _resolve_backend(provider)
    actions = backend_actions.list_backend_actions(backend, include_schemas=include_schemas)
    if page_token:
        actions = [action for action in actions if str(action["action_id"]) > page_token]
    rows = actions[: page_size + 1]
    next_page_token = None
    if len(rows) > page_size:
        next_page_token = str(rows[page_size - 1]["action_id"])
        rows = rows[:page_size]
    return rows, next_page_token


def get_action(action_id: str, *, provider: str | None = None) -> dict[str, Any]:
    return backend_actions.get_backend_action(action_id, _resolve_backend(provider))


def list_config_schemas(
    *,
    page_size: int = 50,
    page_token: str | None = None,
    include_schemas: bool = True,
    provider: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    backend = _resolve_backend(provider)
    rows = backend_config.list_backend_config_schemas(
        backend,
        include_schemas=include_schemas,
    )
    if page_token:
        rows = [row for row in rows if str(row["config_id"]) > page_token]
    page = rows[: page_size + 1]
    next_page_token = None
    if len(page) > page_size:
        next_page_token = str(page[page_size - 1]["config_id"])
        page = page[:page_size]
    return page, next_page_token


def get_config_schema(config_id: str, *, provider: str | None = None) -> dict[str, Any]:
    return backend_config.get_backend_config_schema(config_id, _resolve_backend(provider))


def list_artifact_contracts(
    *,
    page_size: int = 50,
    page_token: str | None = None,
    provider: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    backend = _resolve_backend(provider)
    rows = backend_artifacts.list_backend_artifact_contracts(backend)
    if page_token:
        rows = [row for row in rows if str(row["contract_id"]) > page_token]
    page = rows[: page_size + 1]
    next_page_token = None
    if len(page) > page_size:
        next_page_token = str(page[page_size - 1]["contract_id"])
        page = page[:page_size]
    return page, next_page_token


def get_artifact_contract(contract_id: str, *, provider: str | None = None) -> dict[str, Any]:
    return backend_artifacts.get_backend_artifact_contract(
        contract_id,
        _resolve_backend(provider),
    )


def validate_action(
    action_id: str,
    inputs: dict[str, Any],
    *,
    provider: str | None = None,
) -> dict[str, Any]:
    """:validate is shallow-by-contract, identical across the C++ and
    Python tiers: it confirms the action exists and the envelope is
    well-formed, then echoes the inputs. Deep input validation runs at
    :run / :submit (Python is the single authority, via the bridge) -- a
    deep check here would diverge from the C++ port, which cannot
    replicate engine validation (no sync RPC, no C++ validation engine).
    """
    backend = _resolve_backend(provider)
    # Existence check: raises NotFoundError (-> 404) for an unknown action,
    # matching the C++ kBackendActionsMap lookup.
    backend_actions.get_backend_action(action_id, backend)
    return {
        "action_id": action_id,
        "valid": True,
        "errors": [],
        "normalized_inputs": dict(inputs or {}),
    }


async def submit_action(
    session: AsyncSession,
    *,
    tenant_id: str,
    project_id: str,
    action_id: str,
    inputs: dict[str, Any],
    provider: str | None = None,
) -> tuple[str, list[Any], str]:
    """Submit one backend-native action as an sfmapi job."""
    await project_service.get_project(session, tenant_id=tenant_id, project_id=project_id)
    backend = _resolve_backend(provider)
    action = backend_actions.get_backend_action(action_id, backend)
    # :submit is shallow-by-contract (parity with the C++ port): it confirms
    # the project + action exist and creates the job, passing inputs through
    # unchanged. Input-semantic validation runs at execution in the bridge
    # worker (run_backend_action -> validate_backend_action), the single
    # authority -- a deep check here would diverge from the C++ tier, which
    # creates the job and defers validation to the bridge (no sync RPC, no
    # C++ validation engine). Invalid inputs therefore surface as a failed
    # job, not a rejected submit.
    normalized_inputs = dict(inputs or {})
    task_inputs: dict[str, Any] = {
        "action_id": action_id,
        "project_id": project_id,
        # Arbitrary backend actions may have side effects, so avoid the
        # normal task cache unless a future action-specific contract
        # explicitly declares idempotent cache semantics.
        "run_id": new_id(),
    }
    task_spec: dict[str, Any] = {
        "inputs": normalized_inputs,
        "action": {
            "action_id": action["action_id"],
            "backend": action["backend"],
            "side_effects": action["side_effects"],
        },
    }
    if provider is not None:
        task_spec["provider"] = provider
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
        spec={
            "action_id": action_id,
            "backend": str(getattr(backend, "name", "unknown")),
            **({"provider": provider} if provider is not None else {}),
        },
        nodes=[node],
    )
    return job_id, tasks, str(getattr(backend, "name", "unknown"))
