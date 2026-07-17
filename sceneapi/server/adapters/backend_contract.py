"""Backend authoring contract checks.

These helpers are intentionally stricter than runtime discovery. The
server can tolerate unknown backend details at runtime, but backend
packages should fail CI when they publish extension metadata that
clients cannot use safely.
"""

from __future__ import annotations

from difflib import get_close_matches
from typing import Any

from sceneapi.server.adapters.backend_actions import (
    backend_action_contract_violations,
    list_backend_actions,
)
from sceneapi.server.adapters.backend_artifacts import backend_artifact_contract_violations
from sceneapi.server.adapters.backend_config import (
    backend_config_contract_violations,
    list_backend_config_schemas,
)
from sceneapi.server.core.capabilities import ALL_KNOWN


def _hint_unknown_capability(capability: str) -> str:
    matches = get_close_matches(capability, sorted(ALL_KNOWN), n=1, cutoff=0.78)
    if matches:
        return f"; did you mean {matches[0]!r}?"
    return "; portable capabilities must be added to sceneapi.server.core.capabilities.ALL_KNOWN"


def backend_capability_contract_violations(backend: Any) -> list[str]:
    """Return capability-specific contract violations for backend authors."""
    errors: list[str] = []
    try:
        capabilities = {str(capability) for capability in backend.capabilities()}
    except Exception as exc:
        return [f"capabilities() failed: {exc}"]

    for capability in sorted(capabilities):
        if capability not in ALL_KNOWN:
            errors.append(
                f"{capability}: unknown capability will be dropped from /v1/capabilities"
                f"{_hint_unknown_capability(capability)}"
            )

    try:
        action_ids = {
            str(action["action_id"])
            for action in list_backend_actions(backend, include_schemas=False)
        }
    except Exception:
        action_ids = set()
    for capability in sorted(capabilities):
        for action_id in sorted(action_ids):
            if capability == action_id or capability.startswith(f"{action_id}."):
                errors.append(
                    f"{capability}: backend action ids must not be advertised from "
                    "capabilities(); expose them only through list_backend_actions()"
                )
                break

    try:
        config_rows = list_backend_config_schemas(backend, include_schemas=True)
    except Exception:
        config_rows = []
    config_ids = {str(row.get("config_id")) for row in config_rows if row.get("config_id")}
    for capability in sorted(capabilities):
        for config_id in sorted(config_ids):
            if capability == config_id or capability.startswith(f"{config_id}."):
                errors.append(
                    f"{capability}: backend config schema ids must not be advertised from "
                    "capabilities(); expose backend option schemas only through "
                    "list_backend_config_schemas()"
                )
                break

    for row in config_rows:
        config_capability = row.get("capability")
        if config_capability is not None and str(config_capability) not in capabilities:
            errors.append(
                f"{row.get('config_id')}: config schema references capability "
                f"{config_capability!r}, but capabilities() does not advertise it"
            )

    return errors


def backend_contract_violations(backend: Any) -> list[str]:
    """Return all extension-contract violations for a backend."""
    errors: list[str] = []
    errors.extend(backend_capability_contract_violations(backend))
    errors.extend(backend_action_contract_violations(backend))
    errors.extend(backend_config_contract_violations(backend))
    errors.extend(backend_artifact_contract_violations(backend))
    deduped: list[str] = []
    for error in errors:
        if error not in deduped:
            deduped.append(error)
    return deduped


def assert_backend_contract(backend: Any) -> None:
    """Raise ``AssertionError`` if backend extension metadata is unsafe."""

    violations = backend_contract_violations(backend)
    if violations:
        raise AssertionError(
            "Backend contract violations:\n"
            + "\n".join(f"- {violation}" for violation in violations)
        )


__all__ = [
    "assert_backend_contract",
    "backend_capability_contract_violations",
    "backend_contract_violations",
]
