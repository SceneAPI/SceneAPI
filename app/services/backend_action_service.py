"""Service helpers for backend-native actions."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters import backend_actions, backend_artifacts, backend_config
from app.adapters.registry import get_backend, list_backend_providers
from app.core.config import get_settings
from app.core.config_stages import CONFIG_STAGE_ORDER
from app.core.errors import ValidationError
from app.core.ids import new_id
from app.db.pagination import paginate_sequence
from app.orchestrator.dag import TaskNode, hash_inputs, hash_params
from app.orchestrator.scheduler import submit_job_dag
from app.services import project_service
from sfm_hub.routing import (
    ProviderAmbiguityError,
    ensure_provider_enabled,
    provider_records,
)
from sfm_hub.state import load_state


def _action_pattern_matches(pattern: str, action_id: str) -> bool:
    if pattern.endswith(".*"):
        return action_id.startswith(pattern[:-1])
    return pattern == action_id


def _action_provider_selector(row: Any) -> str:
    return f"{row.provider.provider_id}@{row.plugin_id}"


def _action_provider_registered(row: Any) -> bool:
    registered = set(list_backend_providers())
    return row.provider.provider_id in registered or _action_provider_selector(row) in registered


def _matches_action_provider(rows: list[Any], selector: str) -> list[Any]:
    provider_id, sep, plugin_id = selector.partition("@")
    return [
        row
        for row in rows
        if row.provider.provider_id == provider_id and (not sep or row.plugin_id == plugin_id)
    ]


def _profile_names(
    state: Any,
    *,
    project_id: str | None = None,
    workspace: str | None = None,
) -> list[str]:
    names: list[str] = []
    if project_id and project_id in state.project_profiles:
        names.append(state.project_profiles[project_id])
    if workspace and workspace in state.workspace_profiles:
        names.append(state.workspace_profiles[workspace])
    if state.default_profile:
        names.append(state.default_profile)
    return names


def _single_action_provider_or_raise(
    rows: list[Any],
    *,
    requested_provider: str | None = None,
) -> str:
    if len({(row.provider.provider_id, row.plugin_id) for row in rows}) != 1:
        raise ProviderAmbiguityError(
            "backend_action", [_action_provider_selector(row) for row in rows]
        )
    if requested_provider and "@" in requested_provider:
        return requested_provider
    registered = set(list_backend_providers())
    bare = rows[0].provider.provider_id
    return bare if bare in registered else _action_provider_selector(rows[0])


def _reject_ambiguous_external_provider(provider: str | None) -> None:
    if not provider or "@" in provider:
        return
    state = load_state()
    matches = [row for row in provider_records(state=state) if row.provider.provider_id == provider]
    if len({(row.provider.provider_id, row.plugin_id) for row in matches}) > 1:
        raise ProviderAmbiguityError(
            "backend",
            [_action_provider_selector(row) for row in matches],
        )


def _resolve_action_provider(
    action_id: str,
    provider: str | None,
    *,
    project_id: str | None = None,
    workspace: str | None = None,
) -> str | None:
    state = load_state()
    candidates = [
        row
        for row in provider_records(state=state)
        if _action_provider_registered(row)
        and any(
            _action_pattern_matches(pattern, action_id) for pattern in row.provider.backend_actions
        )
    ]
    if not candidates:
        return provider
    if provider:
        matches = _matches_action_provider(candidates, provider)
        if not matches:
            candidate_selectors = sorted(_action_provider_selector(row) for row in candidates)
            raise KeyError(
                f"provider {provider!r} is not enabled for backend_action; "
                f"candidates: {', '.join(candidate_selectors)}"
            )
        return _single_action_provider_or_raise(matches, requested_provider=provider)
    for profile_name in _profile_names(
        state,
        project_id=project_id,
        workspace=workspace,
    ):
        profile = state.profiles.get(profile_name)
        if profile is None:
            continue
        for provider_id in profile.routes.get("actions", []):
            matches = _matches_action_provider(candidates, provider_id)
            if matches:
                return _single_action_provider_or_raise(
                    matches,
                    requested_provider=provider_id,
                )
    for provider_id in state.provider_priority:
        matches = _matches_action_provider(candidates, provider_id)
        if matches:
            return _single_action_provider_or_raise(
                matches,
                requested_provider=provider_id,
            )
    return _single_action_provider_or_raise(candidates)


def _resolve_backend(
    provider: str | None = None,
    *,
    action_id: str | None = None,
    project_id: str | None = None,
    workspace: str | None = None,
) -> tuple[Any, str | None]:
    try:
        if action_id is not None:
            provider = _resolve_action_provider(
                action_id,
                provider,
                project_id=project_id,
                workspace=workspace,
            )
        else:
            _reject_ambiguous_external_provider(provider)
        if provider is not None:
            ensure_provider_enabled(provider)
        return get_backend(provider=provider), provider
    except KeyError as exc:
        raise ValidationError(str(exc)) from exc
    except ProviderAmbiguityError as exc:
        raise ValidationError(str(exc)) from exc


def backend_summary(*, provider: str | None = None) -> dict[str, Any]:
    backend, _ = _resolve_backend(provider)
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
    backend, _ = _resolve_backend(provider)
    actions = backend_actions.list_backend_actions(backend, include_schemas=include_schemas)
    if page_token:
        actions = [action for action in actions if str(action["action_id"]) > page_token]
    return paginate_sequence(
        actions,
        page_size=page_size,
        token_for=lambda action: str(action["action_id"]),
    )


def get_action(action_id: str, *, provider: str | None = None) -> dict[str, Any]:
    backend, _ = _resolve_backend(provider)
    return backend_actions.get_backend_action(action_id, backend)


def list_config_schemas(
    *,
    page_size: int = 50,
    page_token: str | None = None,
    include_schemas: bool = True,
    provider: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    backend, _ = _resolve_backend(provider)
    rows = backend_config.list_backend_config_schemas(
        backend,
        include_schemas=include_schemas,
    )
    if page_token:
        after = _descriptor_page_token_key(rows, page_token, "config_id")
        rows = [row for row in rows if _descriptor_page_key(row, "config_id") > after]
    return paginate_sequence(
        rows,
        page_size=page_size,
        token_for=lambda row: _encode_descriptor_page_token(row, "config_id"),
    )


def get_config_schema(config_id: str, *, provider: str | None = None) -> dict[str, Any]:
    backend, _ = _resolve_backend(provider)
    return backend_config.get_backend_config_schema(config_id, backend)


def _descriptor_page_key(row: dict[str, Any], id_field: str) -> tuple[int, str]:
    stage = str(row.get("stage") or "")
    return CONFIG_STAGE_ORDER.get(stage, 999), str(row.get(id_field) or "")


def _encode_descriptor_page_token(row: dict[str, Any], id_field: str) -> str:
    rank, descriptor_id = _descriptor_page_key(row, id_field)
    return f"{rank}:{descriptor_id}"


def _descriptor_page_token_key(
    rows: list[dict[str, Any]],
    token: str,
    id_field: str,
) -> tuple[int, str]:
    rank_text, sep, descriptor_id = token.partition(":")
    if sep:
        try:
            return int(rank_text), descriptor_id
        except ValueError:
            return 999, token
    for row in rows:
        if str(row.get(id_field) or "") == token:
            return _descriptor_page_key(row, id_field)
    return 999, token


def list_artifact_contracts(
    *,
    page_size: int = 50,
    page_token: str | None = None,
    provider: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    backend, _ = _resolve_backend(provider)
    rows = backend_artifacts.list_backend_artifact_contracts(backend)
    if page_token:
        after = _descriptor_page_token_key(rows, page_token, "contract_id")
        rows = [row for row in rows if _descriptor_page_key(row, "contract_id") > after]
    return paginate_sequence(
        rows,
        page_size=page_size,
        token_for=lambda row: _encode_descriptor_page_token(row, "contract_id"),
    )


def get_artifact_contract(contract_id: str, *, provider: str | None = None) -> dict[str, Any]:
    backend, _ = _resolve_backend(provider)
    return backend_artifacts.get_backend_artifact_contract(
        contract_id,
        backend,
    )


def validate_action(
    action_id: str,
    inputs: dict[str, Any],
    *,
    provider: str | None = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    """:validate is shallow-by-contract, identical across the C++ and
    Python tiers: it confirms the action exists and the envelope is
    well-formed, then echoes the inputs. Deep input validation runs at
    :run / :submit (Python is the single authority, via the bridge) -- a
    deep check here would diverge from the C++ port, which cannot
    replicate engine validation (no sync RPC, no C++ validation engine).
    """
    backend, _ = _resolve_backend(
        provider,
        action_id=action_id,
        project_id=project_id,
        workspace=str(get_settings().workspace_root),
    )
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
) -> tuple[str, list[Any], str, str | None]:
    """Submit one backend-native action as an sfmapi job."""
    await project_service.get_project(session, tenant_id=tenant_id, project_id=project_id)
    backend, resolved_provider = _resolve_backend(
        provider,
        action_id=action_id,
        project_id=project_id,
        workspace=str(get_settings().workspace_root),
    )
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
    if resolved_provider is not None:
        task_spec["provider"] = resolved_provider
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
            **({"provider": resolved_provider} if resolved_provider is not None else {}),
        },
        nodes=[node],
    )
    return job_id, tasks, str(getattr(backend, "name", "unknown")), resolved_provider
