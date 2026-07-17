"""COLMAP command/action surface — the sfmapi core action standard.

Companion to :mod:`sceneapi.contracts.colmap_db`. That module owns the COLMAP
*data* format (the scene database); this one owns the COLMAP *command*
surface: the ``action_id`` namespace COLMAP-family backends expose, the
closed set of command categories, the read-only / GPU classification,
and the input-schema *kind* that drives validation.

Ownership: owned here. sfmapi defines the standard; the ``sfmapi_colmap``
plugin (and any COLMAP-family backend) conforms. Like ``colmap_db`` this
is a *data standard*, not a dependency — core declares it as plain
constants and never imports the plugin or links the COLMAP binary. The
per-command native option schema is *runtime* data the installed backend
reports (``colmap_command_schema``); the standard defines the stable
vocabulary and the CLI input-schema shape, not the flags a given build
happens to have.

This is the declared off-wire contract behind the COLMAP action layer:
``tools/gen_contracts.py`` serializes :func:`contract_dict` to JSON + a
C++ ``.inc`` and check_sync's ``contract-parity`` gate pins the embedded
copy byte-identical to this source of truth.

Why a standard, not a plugin special-case: the generic action adapter
(:mod:`sceneapi.server.adapters.backend_actions`) dispatches on the schema *kind*
declared here, not on a hardcoded ``startswith("colmap.")``. COLMAP's
specialness is "the one action standard whose vocabulary ships in core",
expressed through the same contract mechanism as every other.
"""

from __future__ import annotations

from typing import Any

from sceneapi.server.core.errors import ValidationError

# --- action namespace + validation kind -----------------------------------

# action_id prefix for every COLMAP command action
# (e.g. "colmap.feature_extractor"). Declared here so the generic action
# adapter keys off the schema KIND below rather than this literal string.
ACTION_NAMESPACE = "colmap"

# How a COLMAP action's inputs are validated: "cli" == named options +
# positional args (the CLI input-schema kind), as opposed to a plain
# "json" object schema. The generic validator dispatches on this kind,
# not on the backend name.
INPUT_SCHEMA_KIND = "cli"

# --- command classification (the closed vocabulary) ------------------------

# Commands with no write side effects: idempotent, not long-running,
# served with side_effects="read".
READ_ONLY_COMMANDS = frozenset({"help", "version", "model_analyzer", "model_comparer"})

# Commands that do not require a GPU: the read-only set plus the CPU-only
# database-maintenance command.
GPU_EXEMPT_COMMANDS = READ_ONLY_COMMANDS | {"database_cleaner"}

# The closed set of categories :func:`category_for` may return. Pinned by
# the contract so the served "category" field stays a known vocabulary.
CATEGORIES = frozenset({"matching", "features", "mapping", "model", "dense", "database", "utility"})


def category_for(command: str) -> str:
    """Classify a COLMAP command into exactly one :data:`CATEGORIES` member."""
    if "matcher" in command or "verifier" in command:
        return "matching"
    if command in {"feature_extractor", "feature_importer"}:
        return "features"
    if "mapper" in command or command in {"point_triangulator", "bundle_adjuster"}:
        return "mapping"
    if command.startswith("model_") or command in {"image_registrator", "image_deleter"}:
        return "model"
    if command in {"patch_match_stereo", "stereo_fusion", "poisson_mesher", "delaunay_mesher"}:
        return "dense"
    if command.startswith("database_"):
        return "database"
    return "utility"


def is_read_only(command: str) -> bool:
    """Whether ``command`` has no write side effects."""
    return command in READ_ONLY_COMMANDS


def requires_gpu(command: str) -> bool:
    """Whether ``command`` needs a GPU (everything but the exempt set)."""
    return command not in GPU_EXEMPT_COMMANDS


# --- CLI input validation (the "cli" kind's reference semantics) -----------
#
# Validate an action's inputs against the backend-reported native option
# schema. Pure: takes the runtime native_schema + inputs, returns a
# {valid, errors, normalized_inputs} verdict. No HTTP / backend coupling,
# so the generic adapter can delegate here by schema kind rather than by
# backend name.


def _normalize_option_key(key: str) -> str:
    return key.strip().lstrip("-").replace("-", "_").lower()


def _option_lookup(native_schema: dict[str, Any]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for option in native_schema.get("options") or []:
        names = [str(option.get("name") or "")]
        names.extend(str(flag).lstrip("-") for flag in option.get("flags") or [])
        for name in names:
            if name:
                lookup[_normalize_option_key(name)] = option
    return lookup


def split_cli_inputs(inputs: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Split inputs into (named options, positional args). Shared by
    validation and execution so both interpret the CLI shape identically."""
    data = dict(inputs or {})
    positional_raw = data.pop("positional_args", data.pop("positional", []))
    if positional_raw is None:
        positional: list[str] = []
    elif isinstance(positional_raw, list):
        positional = [str(item) for item in positional_raw]
    else:
        raise ValidationError("positional_args must be an array of strings")

    if set(data) == {"options"} and isinstance(data.get("options"), dict):
        options = dict(data["options"])
    else:
        options = data
    return options, positional


def _validate_scalar(value: Any, option: dict[str, Any], command: str) -> str | None:
    name = str(option.get("name") or "option")
    schema = option.get("schema") or {}
    expected = str(option.get("type") or schema.get("type") or "string")
    choices = [str(choice) for choice in option.get("choices") or schema.get("enum") or []]

    if choices and str(value) not in choices:
        return f"--{name} for COLMAP {command} must be one of: {', '.join(choices)}"
    if (
        expected == "boolean"
        and not isinstance(value, bool)
        and str(value).lower() not in {"0", "1", "true", "false", "yes", "no", "on", "off"}
    ):
        return f"--{name} for COLMAP {command} expects boolean"
    if expected == "integer":
        if isinstance(value, bool):
            return f"--{name} for COLMAP {command} expects integer"
        try:
            int(value)
        except (TypeError, ValueError):
            return f"--{name} for COLMAP {command} expects integer"
    if expected == "number":
        if isinstance(value, bool):
            return f"--{name} for COLMAP {command} expects number"
        try:
            float(value)
        except (TypeError, ValueError):
            return f"--{name} for COLMAP {command} expects number"
    return None


def validate_cli_inputs(
    command: str,
    native_schema: dict[str, Any],
    inputs: dict[str, Any],
) -> dict[str, Any]:
    """Validate ``inputs`` for a COLMAP ``command`` against its native option
    schema. Returns ``{valid, errors, normalized_inputs}`` (the caller adds
    ``action_id``). Unknown options, type mismatches, and missing required
    options are reported; positional args pass through normalized.
    """
    native_schema = native_schema or {}
    lookup = _option_lookup(native_schema)
    options, positional = split_cli_inputs(inputs)
    errors: list[dict[str, str | None]] = []
    normalized: dict[str, Any] = {}
    if positional:
        normalized["positional_args"] = positional

    provided: set[str] = set()
    for raw_key, value in sorted(options.items()):
        if value is None:
            continue
        option = lookup.get(_normalize_option_key(str(raw_key)))
        if option is None:
            errors.append(
                {"field": str(raw_key), "message": f"unknown option for COLMAP {command}"}
            )
            continue
        name = str(option.get("name") or raw_key)
        problem = _validate_scalar(value, option, command)
        if problem:
            errors.append({"field": name, "message": problem})
            continue
        provided.add(name)
        normalized[name] = value

    for option in native_schema.get("options") or []:
        name = str(option.get("name") or "")
        if name and option.get("required") is True and name not in provided:
            errors.append(
                {"field": name, "message": f"missing required option for COLMAP {command}"}
            )

    return {
        "valid": not errors,
        "errors": errors,
        "normalized_inputs": normalized,
    }


# --- declared contract -----------------------------------------------------

CONTRACT_NAME = "colmap_actions"
CONTRACT_SCHEMA_VERSION = 1  # version of THIS serialization shape


def contract_dict() -> dict:
    """The COLMAP action standard as a deterministic, JSON-serializable dict.

    ``tools/gen_contracts.py`` serializes this to JSON + a C++ ``.inc``;
    check_sync's ``contract-parity`` gate keeps the embedded copy
    byte-identical. Ordering is stable (sorted) so the JSON is reproducible.
    """
    return {
        "contract": CONTRACT_NAME,
        "contract_schema_version": CONTRACT_SCHEMA_VERSION,
        "action_namespace": ACTION_NAMESPACE,
        "input_schema_kind": INPUT_SCHEMA_KIND,
        "categories": sorted(CATEGORIES),
        "read_only_commands": sorted(READ_ONLY_COMMANDS),
        "gpu_exempt_commands": sorted(GPU_EXEMPT_COMMANDS),
    }


__all__ = [
    "ACTION_NAMESPACE",
    "CATEGORIES",
    "CONTRACT_NAME",
    "CONTRACT_SCHEMA_VERSION",
    "GPU_EXEMPT_COMMANDS",
    "INPUT_SCHEMA_KIND",
    "READ_ONLY_COMMANDS",
    "category_for",
    "contract_dict",
    "is_read_only",
    "requires_gpu",
    "split_cli_inputs",
    "validate_cli_inputs",
]
