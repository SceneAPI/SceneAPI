"""Runtime provisioning hooks for installed backend plugin packages."""

from __future__ import annotations

import argparse
import importlib
import inspect
import json
import re
from collections.abc import Mapping
from typing import Any

try:
    from app.core.public_outputs import (
        sanitize_public_error_message,
        sanitize_public_outputs,
    )
except Exception:  # pragma: no cover - standalone hub fallback
    def sanitize_public_outputs(value: Any) -> Any:
        return value

    def sanitize_public_error_message(value: Any) -> str:
        return str(value or "")

SENSITIVE_KEY_PARTS = (
    "TOKEN",
    "SECRET",
    "KEY",
    "PASSWORD",
    "CREDENTIAL",
    "AUTH",
    "SIGNATURE",
    "X-AMZ",
    "X-GOOG-SIGNATURE",
    "AWSACCESSKEYID",
    "GOOGLEACCESSID",
    "SIGV4",
)
SIGNED_DROP_KEY_RE = re.compile(
    r"(x-amz|x-goog-signature|awsaccesskeyid|googleaccessid|signature|sigv4|^sig$)",
    re.IGNORECASE,
)


class ProvisioningError(RuntimeError):
    """Raised when a plugin provisioner exists but cannot complete."""


def package_module_name(package_name: str) -> str:
    base_name = package_name.strip().split("[", 1)[0]
    return base_name.replace("-", "_")


def _empty_result(*, warning: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "available": False,
        "provisioned": False,
        "steps": [],
        "env_keys": [],
        "redacted_env": {},
        "outputs": {},
        "warnings": [],
        "metadata": {},
    }
    if warning:
        result["warnings"].append(warning)
    return result


def _normalize_result(value: object) -> dict[str, Any]:
    if value is None:
        return {
            "available": True,
            "provisioned": False,
            "steps": [],
            "env_keys": [],
            "redacted_env": {},
            "outputs": {},
            "warnings": [],
            "metadata": {},
        }
    if not isinstance(value, Mapping):
        raise ProvisioningError("plugin provisioner must return a mapping or None")

    steps = value.get("steps", [])
    warnings = value.get("warnings", [])
    env = value.get("env", {})
    env_keys_value = value.get("env_keys", [])
    redacted_env_value = value.get("redacted_env", {})
    outputs = value.get("outputs", {})
    metadata = value.get("metadata", {})
    if not isinstance(steps, list):
        raise ProvisioningError("plugin provisioner result field 'steps' must be a list")
    if not isinstance(warnings, list):
        raise ProvisioningError("plugin provisioner result field 'warnings' must be a list")
    if not isinstance(env, Mapping):
        raise ProvisioningError("plugin provisioner result field 'env' must be a mapping")
    if not isinstance(env_keys_value, list):
        raise ProvisioningError("plugin provisioner result field 'env_keys' must be a list")
    if not isinstance(redacted_env_value, Mapping):
        raise ProvisioningError("plugin provisioner result field 'redacted_env' must be a mapping")
    if not isinstance(outputs, Mapping):
        raise ProvisioningError("plugin provisioner result field 'outputs' must be a mapping")
    if not isinstance(metadata, Mapping):
        raise ProvisioningError("plugin provisioner result field 'metadata' must be a mapping")

    normalized_steps: list[dict[str, Any]] = []
    for step in steps:
        if not isinstance(step, Mapping):
            raise ProvisioningError("plugin provisioner result steps must be mappings")
        normalized_steps.append(_redact_object({str(key): item for key, item in step.items()}))
    env_keys = [str(key) for key in env] or [str(key) for key in env_keys_value]
    redacted_env = {key: "<redacted>" for key in env_keys}
    if not redacted_env and redacted_env_value:
        redacted_env = {str(key): "<redacted>" for key in redacted_env_value}
        env_keys = list(redacted_env)

    return {
        "available": bool(value.get("available", True)),
        "provisioned": bool(value.get("provisioned", False)),
        "steps": normalized_steps,
        "env_keys": env_keys,
        "redacted_env": redacted_env,
        "outputs": _redact_object({str(key): item for key, item in outputs.items()}),
        "warnings": [_redact_object(str(item)) for item in warnings],
        "metadata": _redact_object({str(key): item for key, item in metadata.items()}),
    }


def run_package_provisioner(
    package_name: str,
    *,
    dry_run: bool,
    force: bool = False,
) -> dict[str, Any]:
    """Run an installed plugin package's optional runtime provisioner."""

    module_name = package_module_name(package_name)
    provisioner_module = f"{module_name}.provisioning"
    try:
        module = importlib.import_module(provisioner_module)
    except ModuleNotFoundError as exc:
        if exc.name in {module_name, provisioner_module}:
            return _empty_result(
                warning=f"package {package_name!r} does not expose {provisioner_module}"
            )
        raise ProvisioningError(sanitize_public_error_message(exc)) from exc

    provision = getattr(module, "provision", None)
    if not callable(provision):
        return _empty_result(
            warning=f"package {package_name!r} exposes no callable provision() hook"
        )

    kwargs: dict[str, Any] = {}
    signature = inspect.signature(provision)
    if "dry_run" in signature.parameters:
        kwargs["dry_run"] = dry_run
    if "force" in signature.parameters:
        kwargs["force"] = force

    try:
        return _normalize_result(provision(**kwargs))
    except ProvisioningError:
        raise
    except Exception as exc:  # pragma: no cover - exercised by plugin packages
        raise ProvisioningError(str(exc)) from exc


def normalize_provisioning_result(value: object) -> dict[str, Any]:
    """Normalize and redact a provisioner result at API/service boundaries."""

    return _normalize_result(value)


def planned_package_provisioning(package_name: str) -> dict[str, Any]:
    module_name = package_module_name(package_name)
    return {
        "available": False,
        "provisioned": False,
        "steps": [
            {
                "name": "plugin_provisioner",
                "action": f"import {module_name}.provisioning and run provision()",
                "status": "planned",
            }
        ],
        "env_keys": [],
        "redacted_env": {},
        "outputs": {},
        "warnings": [],
        "metadata": {},
    }


def _is_sensitive_key(key: str) -> bool:
    upper = key.upper()
    return upper == "SIG" or any(part in upper for part in SENSITIVE_KEY_PARTS)


def _redact_object(value: object, *, key: str = "") -> Any:
    if key and _is_sensitive_key(key):
        return "<redacted>"
    if isinstance(value, Mapping):
        return {
            str(item_key): _redact_object(item, key=str(item_key))
            for item_key, item in value.items()
            if not SIGNED_DROP_KEY_RE.search(str(item_key))
        }
    if isinstance(value, list):
        return [_redact_object(item) for item in value]
    if isinstance(value, str):
        return sanitize_public_outputs(value)
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a backend plugin runtime provisioner.")
    parser.add_argument("package_name")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    result = run_package_provisioner(args.package_name, dry_run=args.dry_run, force=args.force)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
