"""Generic adapter layer for backend-native actions.

This module keeps backend extension discovery out of the core
capability registry. Backends can implement a generic action provider
API, while existing COLMAP demo backends are adapted from their
``list_colmap_commands`` / ``colmap_command_schema`` /
``run_colmap_command`` helpers.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast
from urllib.parse import quote

from sfmapi.server.adapters.registry import get_backend
from sfmapi.server.core import colmap_actions
from sfmapi.server.core.capabilities import ALL_KNOWN
from sfmapi.server.core.errors import NotFoundError, ValidationError


class BackendActionProvider(Protocol):
    """Optional structural protocol implemented by richer backends."""

    def list_backend_actions(self, *, include_schemas: bool = False) -> list[dict[str, Any]]: ...

    def get_backend_action(self, action_id: str) -> dict[str, Any]: ...

    def validate_backend_action(self, action_id: str, inputs: dict[str, Any]) -> dict[str, Any]: ...

    def run_backend_action(
        self,
        action_id: str,
        inputs: dict[str, Any],
        *,
        workspace: Path | None = None,
        progress: Any | None = None,
    ) -> dict[str, Any]: ...


ACTION_STABILITIES = {"stable", "experimental", "backend_extension", "deprecated"}
ACTION_SIDE_EFFECTS = {"none", "read", "write", "unknown"}


def _backend_name(backend: Any) -> str:
    return str(getattr(backend, "name", "unknown"))


def _link(action_id: str) -> dict[str, dict[str, str]]:
    encoded = quote(action_id, safe="")
    return {
        "self": {"href": f"/v1/backend/actions/{encoded}"},
        "validate": {"href": f"/v1/backend/actions/{encoded}:validate"},
        "run": {"href": f"/v1/backend/actions/{encoded}:run"},
    }


def _normalize_descriptor(
    raw: dict[str, Any],
    *,
    backend: Any,
    include_schema: bool,
) -> dict[str, Any]:
    action_id = str(raw.get("action_id") or raw.get("id") or raw.get("name") or "").strip()
    if not action_id:
        raise ValidationError("backend action descriptor missing action_id")
    if "display_name" in raw:
        display_name = raw.get("display_name")
    elif "title" in raw:
        display_name = raw.get("title")
    else:
        display_name = action_id
    input_schema = raw.get("input_schema") if include_schema else None
    output_schema = raw.get("output_schema") if include_schema else None
    return {
        "action_id": action_id,
        "backend": str(raw.get("backend") or _backend_name(backend)),
        "display_name": "" if display_name is None else str(display_name),
        "description": raw.get("description"),
        "category": raw.get("category"),
        "stability": raw.get("stability") or "backend_extension",
        "side_effects": raw.get("side_effects") or "unknown",
        "long_running": bool(raw.get("long_running", True)),
        "supports_progress": bool(raw.get("supports_progress", False)),
        "idempotent": bool(raw.get("idempotent", False)),
        "gpu_required": bool(raw.get("gpu_required", True)),
        "required_capabilities": list(raw.get("required_capabilities") or []),
        "input_schema": input_schema,
        "output_schema": output_schema,
        "metadata": dict(raw.get("metadata") or {}),
        "_links": _link(action_id),
    }


def _call_with_supported_kwargs(fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
    """Call ``fn`` with only the optional kwargs its signature accepts."""
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return fn(*args, **kwargs)
    if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()):
        return fn(*args, **kwargs)
    supported = {key: value for key, value in kwargs.items() if key in signature.parameters}
    return fn(*args, **supported)


def _schema_for_colmap_command(schema: dict[str, Any]) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "positional_args": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Optional positional arguments passed before named options.",
        }
    }
    required: list[str] = []
    for option in schema.get("options") or []:
        name = str(option.get("name") or "").strip()
        if not name:
            continue
        properties[name] = dict(option.get("schema") or {"type": "string"})
        description = option.get("description")
        if description and "description" not in properties[name]:
            properties[name]["description"] = description
        if option.get("required") is True:
            required.append(name)
    out: dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
    }
    if required:
        out["required"] = required
    return out


def _colmap_descriptor(
    backend: Any,
    command: str,
    *,
    schema: dict[str, Any] | None = None,
    include_schema: bool = False,
) -> dict[str, Any]:
    read_only = colmap_actions.is_read_only(command)
    metadata: dict[str, Any] = {"family": "colmap", "command": command}
    if schema is not None:
        metadata["native_schema"] = schema
        metadata["schema_source"] = schema.get("schema_source")
        metadata["option_count"] = schema.get("option_count", len(schema.get("options") or []))
    return {
        "action_id": f"colmap.{command}",
        "backend": _backend_name(backend),
        "display_name": f"COLMAP {command}",
        "description": f"Run the upstream COLMAP `{command}` command through the active backend.",
        "category": colmap_actions.category_for(command),
        "stability": "backend_extension",
        "side_effects": "read" if read_only else "write",
        "long_running": not read_only,
        "supports_progress": False,
        "idempotent": read_only,
        "gpu_required": colmap_actions.requires_gpu(command),
        "required_capabilities": [],
        "input_schema": _schema_for_colmap_command(schema) if include_schema and schema else None,
        "output_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "returncode": {"type": "integer"},
                "stdout": {"type": "string"},
                "stderr": {"type": "string"},
            },
        }
        if include_schema
        else None,
        "metadata": metadata,
        "_links": _link(f"colmap.{command}"),
    }


def _dedupe(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for action in actions:
        action_id = str(action["action_id"])
        if action_id not in by_id:
            by_id[action_id] = action
    return [by_id[key] for key in sorted(by_id)]


def list_backend_actions(
    backend: Any | None = None,
    *,
    include_schemas: bool = False,
) -> list[dict[str, Any]]:
    """List normalized backend action descriptors."""
    backend = backend or get_backend()
    actions: list[dict[str, Any]] = []

    generic = getattr(backend, "list_backend_actions", None)
    if callable(generic):
        for raw in _call_with_supported_kwargs(generic, include_schemas=include_schemas):
            actions.append(
                _normalize_descriptor(raw, backend=backend, include_schema=include_schemas)
            )

    for namespace in _ACTION_NAMESPACES.values():
        actions.extend(namespace.list_descriptors(backend, include_schemas=include_schemas))

    return _dedupe(actions)


def has_backend_actions(backend: Any | None = None) -> bool:
    """Cheap capability probe for the public capabilities envelope."""
    try:
        return bool(list_backend_actions(backend, include_schemas=False))
    except Exception:
        return False


def _colmap_schema(backend: Any, command: str) -> dict[str, Any]:
    schema_fn = getattr(backend, "colmap_command_schema", None)
    if not callable(schema_fn):
        raise NotFoundError(f"Backend action colmap.{command} not found")
    return cast(dict[str, Any], schema_fn(command))


@dataclass(frozen=True)
class _ActionNamespace:
    """How the generic adapter handles one namespaced action standard.

    Dispatch is keyed by the action_id's namespace (the segment before the
    first dot), which the standard module owns
    (e.g. ``colmap_actions.ACTION_NAMESPACE``). Adding a CLI-family
    standard is a registry entry, not another ``startswith`` branch that
    names a specific backend in this generic adapter.
    """

    list_descriptors: Callable[..., list[dict[str, Any]]]
    build_descriptor: Callable[[Any, str], dict[str, Any]]
    validate: Callable[[str, dict[str, Any], dict[str, Any]], dict[str, Any]]
    run: Callable[[Any, str, dict[str, Any]], dict[str, Any]]


def _list_colmap_descriptors(backend: Any, *, include_schemas: bool) -> list[dict[str, Any]]:
    list_fn = getattr(backend, "list_colmap_commands", None)
    if not callable(list_fn):
        return []
    descriptors: list[dict[str, Any]] = []
    for command in list_fn():
        normalized = str(command).replace("-", "_").lower()
        schema = _colmap_schema(backend, normalized) if include_schemas else None
        descriptors.append(
            _colmap_descriptor(backend, normalized, schema=schema, include_schema=include_schemas)
        )
    return descriptors


def _build_colmap_descriptor(backend: Any, command: str) -> dict[str, Any]:
    normalized = command.replace("-", "_").lower()
    schema = _colmap_schema(backend, normalized)
    return _colmap_descriptor(backend, normalized, schema=schema, include_schema=True)


def _run_colmap_command(backend: Any, command: str, inputs: dict[str, Any]) -> dict[str, Any]:
    run_colmap = getattr(backend, "run_colmap_command", None)
    if not callable(run_colmap):
        action_id = f"{colmap_actions.ACTION_NAMESPACE}.{command}"
        raise NotFoundError(f"Backend action {action_id!r} not found")
    options, positional = colmap_actions.split_cli_inputs(inputs)
    return cast(dict[str, Any], run_colmap(command, options=options, positional=positional))


# Namespaced action standards. The descriptor assembly + execution stay
# here (HTTP/backend-coupled — _links, backend name, run_colmap_command);
# the standard module owns the namespace and the pure input validator.
_ACTION_NAMESPACES: dict[str, _ActionNamespace] = {
    colmap_actions.ACTION_NAMESPACE: _ActionNamespace(
        list_descriptors=_list_colmap_descriptors,
        build_descriptor=_build_colmap_descriptor,
        validate=colmap_actions.validate_cli_inputs,
        run=_run_colmap_command,
    ),
}


def _resolve_action_namespace(action_id: str) -> _ActionNamespace | None:
    return _ACTION_NAMESPACES.get(action_id.partition(".")[0])


def get_backend_action(action_id: str, backend: Any | None = None) -> dict[str, Any]:
    """Read one normalized action descriptor with its schema."""
    backend = backend or get_backend()
    generic_get = getattr(backend, "get_backend_action", None)
    if callable(generic_get):
        try:
            return _normalize_descriptor(
                generic_get(action_id),
                backend=backend,
                include_schema=True,
            )
        except NotFoundError:
            pass

    for action in list_backend_actions(backend, include_schemas=True):
        if action["action_id"] == action_id:
            return action

    handler = _resolve_action_namespace(action_id)
    if handler is not None:
        return handler.build_descriptor(backend, action_id.partition(".")[2])

    raise NotFoundError(f"Backend action {action_id!r} not found")


def _validate_json_inputs(
    action_id: str,
    input_schema: dict[str, Any],
    inputs: dict[str, Any],
) -> dict[str, Any]:
    """The "json" kind: validate inputs against an action's declared object
    schema, reusing the same engine as backend config-option validation so
    both interpret a JSON schema identically."""
    from sfmapi.server.adapters import backend_config  # local: avoid import cycle

    values = dict(inputs or {})
    errors = backend_config.validate_against_schema(action_id, values, input_schema)
    return {
        "action_id": action_id,
        "valid": not errors,
        "errors": errors,
        "normalized_inputs": values,
    }


def validate_backend_action(
    action_id: str,
    inputs: dict[str, Any],
    backend: Any | None = None,
) -> dict[str, Any]:
    """Validate input for one action without submitting work."""
    backend = backend or get_backend()
    descriptor = get_backend_action(action_id, backend)

    generic_validate = getattr(backend, "validate_backend_action", None)
    if callable(generic_validate):
        try:
            result = generic_validate(action_id, inputs)
        except ValidationError as exc:
            return {
                "action_id": action_id,
                "valid": False,
                "errors": [{"field": None, "message": exc.detail}],
                "normalized_inputs": {},
            }
        if isinstance(result, dict) and "valid" in result:
            return {
                "action_id": action_id,
                "valid": bool(result.get("valid")),
                "errors": list(result.get("errors") or []),
                "normalized_inputs": dict(result.get("normalized_inputs") or inputs),
            }

    handler = _resolve_action_namespace(action_id)
    if handler is not None:
        # cli kind: namespaced standard (e.g. colmap) validates against the
        # backend-reported native option schema.
        native_schema = descriptor.get("metadata", {}).get("native_schema") or {}
        result = handler.validate(action_id.partition(".")[2], native_schema, inputs)
        return {"action_id": action_id, **result}

    input_schema = descriptor.get("input_schema")
    if isinstance(input_schema, dict) and isinstance(input_schema.get("properties"), dict):
        # json kind: validate against the action's declared object schema.
        return _validate_json_inputs(action_id, input_schema, inputs)

    # passthrough kind: the action declares no input schema, so inputs pass
    # through unchecked. Explicit by contract -- a descriptor whose
    # input_schema is null opts out of validation, distinct from one that
    # has simply not declared a schema yet.
    return {
        "action_id": action_id,
        "valid": True,
        "errors": [],
        "normalized_inputs": dict(inputs or {}),
    }


def run_backend_action(
    action_id: str,
    inputs: dict[str, Any],
    *,
    workspace: Path | None = None,
    progress: Any | None = None,
    backend: Any | None = None,
) -> dict[str, Any]:
    """Execute an action through the backend implementation."""
    backend = backend or get_backend()
    validation = validate_backend_action(action_id, inputs, backend)
    if not validation.get("valid"):
        details = "; ".join(str(error.get("message")) for error in validation.get("errors") or [])
        raise ValidationError(details or f"invalid inputs for backend action {action_id!r}")
    normalized_inputs = dict(validation.get("normalized_inputs") or inputs or {})

    generic_run = getattr(backend, "run_backend_action", None)
    if callable(generic_run):
        return cast(
            dict[str, Any],
            _call_with_supported_kwargs(
                generic_run,
                action_id,
                normalized_inputs,
                workspace=workspace,
                progress=progress,
            ),
        )

    handler = _resolve_action_namespace(action_id)
    if handler is not None:
        return handler.run(backend, action_id.partition(".")[2], normalized_inputs)

    raise NotFoundError(f"Backend action {action_id!r} not found")


def backend_action_contract_violations(backend: Any) -> list[str]:
    """Return backend action contract violations for backend authors.

    Backend packages can use this in their own test suites:

    .. code-block:: python

        from sfmapi.server.adapters.backend_actions import assert_backend_action_contract

        def test_backend_contract():
            assert_backend_action_contract(MyBackend())

    This catches the most common extension-surface mistake: putting
    backend-native action ids such as ``vendor.tool`` into
    ``capabilities()`` instead of exposing them through
    ``list_backend_actions()``.
    """

    errors: list[str] = []
    raw_action_ids: list[str] = []
    generic_list = getattr(backend, "list_backend_actions", None)
    if callable(generic_list):
        try:
            raw_actions = list(generic_list())
        except Exception as exc:
            return [f"list_backend_actions() failed: {exc}"]
        for index, raw in enumerate(raw_actions):
            if not isinstance(raw, dict):
                errors.append(f"action[{index}]: descriptor must be an object")
                continue
            action_id = str(raw.get("action_id") or raw.get("id") or raw.get("name") or "")
            if action_id:
                raw_action_ids.append(action_id)
            if "display_name" in raw and not str(raw.get("display_name") or "").strip():
                errors.append(f"{action_id or f'action[{index}]'}: display_name is required")
            label = action_id or f"action[{index}]"
            if "stability" in raw and str(raw.get("stability")) not in ACTION_STABILITIES:
                errors.append(f"{label}: stability must be one of {sorted(ACTION_STABILITIES)}")
            if "side_effects" in raw and str(raw.get("side_effects")) not in ACTION_SIDE_EFFECTS:
                errors.append(f"{label}: side_effects must be one of {sorted(ACTION_SIDE_EFFECTS)}")
            input_schema = raw.get("input_schema")
            if input_schema is not None and not isinstance(input_schema, dict):
                errors.append(f"{label}: input_schema must be an object or null")
            output_schema = raw.get("output_schema")
            if output_schema is not None and not isinstance(output_schema, dict):
                errors.append(f"{label}: output_schema must be an object or null")
            for capability in raw.get("required_capabilities") or []:
                cap = str(capability)
                if cap not in ALL_KNOWN:
                    errors.append(
                        f"{label}: required_capabilities contains non-portable capability "
                        f"{cap!r}; backend-native prerequisites belong in action metadata"
                    )
        raw_duplicates = sorted(
            {action_id for action_id in raw_action_ids if raw_action_ids.count(action_id) > 1}
        )
        for action_id in raw_duplicates:
            errors.append(f"{action_id}: duplicate action_id")

    try:
        actions = list_backend_actions(backend, include_schemas=True)
    except Exception as exc:
        return [f"list_backend_actions() failed: {exc}"]

    action_ids: list[str] = []
    for index, action in enumerate(actions):
        action_id = str(action.get("action_id") or "")
        label = action_id or f"action[{index}]"
        if not action_id:
            errors.append(f"{label}: action_id is required")
            continue
        action_ids.append(action_id)
        if "." not in action_id:
            errors.append(f"{label}: action_id should be namespaced, e.g. vendor.operation")
        if not str(action.get("display_name") or "").strip():
            errors.append(f"{label}: display_name is required")
        if str(action.get("stability")) not in ACTION_STABILITIES:
            errors.append(f"{label}: stability must be one of {sorted(ACTION_STABILITIES)}")
        if str(action.get("side_effects")) not in ACTION_SIDE_EFFECTS:
            errors.append(f"{label}: side_effects must be one of {sorted(ACTION_SIDE_EFFECTS)}")
        input_schema = action.get("input_schema")
        if input_schema is not None and not isinstance(input_schema, dict):
            errors.append(f"{label}: input_schema must be an object or null")
        output_schema = action.get("output_schema")
        if output_schema is not None and not isinstance(output_schema, dict):
            errors.append(f"{label}: output_schema must be an object or null")
        for capability in action.get("required_capabilities") or []:
            cap = str(capability)
            if cap not in ALL_KNOWN:
                errors.append(
                    f"{label}: required_capabilities contains non-portable capability {cap!r}; "
                    "backend-native prerequisites belong in action metadata"
                )

    duplicates = sorted({action_id for action_id in action_ids if action_ids.count(action_id) > 1})
    for action_id in duplicates:
        errors.append(f"{action_id}: duplicate action_id")

    try:
        capabilities = set(backend.capabilities())
    except Exception as exc:
        errors.append(f"capabilities() failed: {exc}")
        capabilities = set()
    for capability in sorted(capabilities):
        for action_id in action_ids:
            if capability == action_id or capability.startswith(f"{action_id}."):
                errors.append(
                    f"{capability}: backend action ids must not be advertised from "
                    "capabilities(); expose them only through list_backend_actions()"
                )
                break

    return errors


def assert_backend_action_contract(backend: Any) -> None:
    """Raise ``AssertionError`` if a backend mixes capabilities and actions."""

    violations = backend_action_contract_violations(backend)
    if violations:
        raise AssertionError(
            "Backend action contract violations:\n"
            + "\n".join(f"- {violation}" for violation in violations)
        )


__all__ = [
    "BackendActionProvider",
    "assert_backend_action_contract",
    "backend_action_contract_violations",
    "get_backend_action",
    "has_backend_actions",
    "list_backend_actions",
    "run_backend_action",
    "validate_backend_action",
]
