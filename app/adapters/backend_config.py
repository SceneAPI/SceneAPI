"""Backend-specific configuration discovery and validation.

Portable sfmapi stage specs intentionally keep only the knobs that
work across engines. Backend packages can expose richer option schemas
here so clients can build provider-specific forms while still sending
those options through the stable ``backend_options`` envelope.
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable
from typing import Any, Protocol
from urllib.parse import quote

from app.adapters.registry import get_backend
from app.core.capabilities import ALL_KNOWN
from app.core.errors import NotFoundError, ValidationError
from sfm_hub.routing import ensure_provider_enabled


class BackendConfigSchemaProvider(Protocol):
    """Optional structural protocol implemented by richer backends."""

    def list_backend_config_schemas(
        self, *, include_schemas: bool = True
    ) -> list[dict[str, Any]]: ...


_STAGE_ORDER = {
    "features": 10,
    "pairs": 20,
    "matcher": 30,
    "verify": 40,
    "mapping": 50,
    "bundle_adjustment": 60,
    "radiance": 70,
}
_VALID_STAGES = frozenset(_STAGE_ORDER)
# Canonical patterns live in app.core.ids; re-export under the local
# underscore names to keep existing call sites in this file untouched.
from app.core.ids import NAMESPACED_ID_RE as _NAMESPACED_ID_RE  # noqa: E402
from app.core.ids import PROVIDER_ID_RE as _PROVIDER_RE  # noqa: E402

_COLMAP_STAGE_CONFIGS: tuple[tuple[str, str, str, str, str], ...] = (
    ("colmap.features.sift", "features", "features.extract.sift", "colmap", "feature_extractor"),
    ("colmap.pairs.exhaustive", "pairs", "pairs.exhaustive", "colmap", "exhaustive_matcher"),
    ("colmap.pairs.sequential", "pairs", "pairs.sequential", "colmap", "sequential_matcher"),
    ("colmap.pairs.spatial", "pairs", "pairs.spatial", "colmap", "spatial_matcher"),
    ("colmap.pairs.vocabtree", "pairs", "pairs.vocabtree", "colmap", "vocab_tree_matcher"),
    ("colmap.pairs.explicit", "pairs", "pairs.explicit", "colmap", "matches_importer"),
    # from_poses selects pairs by camera-position proximity, reusing the
    # spatial_matcher option surface (same COLMAP command).
    ("colmap.pairs.from_poses", "pairs", "pairs.from_poses", "colmap", "spatial_matcher"),
    ("colmap.matcher.sift", "matcher", "matchers.nn-mutual", "colmap", "exhaustive_matcher"),
    ("colmap.verify", "verify", "matches.verify", "colmap", "geometric_verifier"),
    ("colmap.mapping.incremental", "mapping", "map.incremental", "colmap", "mapper"),
    ("colmap.mapping.global", "mapping", "map.global", "colmap", "global_mapper"),
    ("colmap.mapping.hierarchical", "mapping", "map.hierarchical", "colmap", "hierarchical_mapper"),
    ("colmap.ba.standard", "bundle_adjustment", "ba.standard", "colmap", "bundle_adjuster"),
)

# Public, stable alias of the canonical COLMAP stage-config table. The COLMAP
# family plugins (sfmapi_colmap / sfmapi_pycolmap / sfmapi_colmap_cli) import
# THIS as their single source of truth rather than each keeping a local copy
# (which had already drifted on the from_poses row).
COLMAP_STAGE_CONFIGS = _COLMAP_STAGE_CONFIGS

_RUNTIME_MANAGED_COLMAP_OPTIONS = {
    "database_path",
    "image_path",
    "image_list_path",
    "input_path",
    "input_path1",
    "input_path2",
    "output_path",
    "workspace_path",
    "project_path",
    "match_list_path",
    "help",
    "log_level",
    "log_to_stderr",
    "log_color",
    "log_target",
}


def _backend_name(backend: Any) -> str:
    return str(getattr(backend, "name", "unknown"))


def _link(config_id: str) -> dict[str, dict[str, str]]:
    encoded = quote(config_id, safe="")
    return {
        "self": {"href": f"/v1/backend/config-schemas/{encoded}"},
        "collection": {"href": "/v1/backend/config-schemas"},
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


def _infer_stage(capability: str | None) -> str:
    if not capability:
        return "other"
    if capability.startswith("features."):
        return "features"
    if capability.startswith("pairs."):
        return "pairs"
    if capability.startswith("matchers."):
        return "matcher"
    if capability == "matches.verify":
        return "verify"
    if capability.startswith("map."):
        return "mapping"
    if capability.startswith("ba."):
        return "bundle_adjustment"
    if capability.startswith("radiance."):
        return "radiance"
    return "other"


def _normalize_descriptor(
    raw: dict[str, Any],
    *,
    backend: Any,
    include_schema: bool,
) -> dict[str, Any]:
    config_id = str(raw.get("config_id") or raw.get("id") or raw.get("name") or "").strip()
    if not config_id:
        raise ValidationError("backend config schema descriptor missing config_id")
    capability = raw.get("capability")
    capability = None if capability is None else str(capability)
    provider = raw.get("provider")
    provider = None if provider is None else str(provider)
    schema = raw.get("option_schema", raw.get("schema", raw.get("input_schema")))
    if schema is not None and not isinstance(schema, dict):
        raise ValidationError(f"{config_id}: option_schema must be an object or null")
    return {
        "config_id": config_id,
        "backend": str(raw.get("backend") or _backend_name(backend)),
        "stage": str(raw.get("stage") or _infer_stage(capability)),
        "capability": capability,
        "provider": provider,
        "display_name": raw.get("display_name") or raw.get("title") or config_id,
        "description": raw.get("description"),
        "option_schema": dict(schema or {}) if include_schema else None,
        "defaults": dict(raw.get("defaults") or {}),
        "metadata": dict(raw.get("metadata") or {}),
        "_links": _link(config_id),
    }


def _dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        by_id.setdefault(str(row["config_id"]), row)
    return sorted(
        by_id.values(),
        key=lambda item: (_STAGE_ORDER.get(str(item.get("stage")), 999), str(item["config_id"])),
    )


def _schema_for_colmap_backend_options(schema: dict[str, Any]) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    for option in schema.get("options") or []:
        name = str(option.get("name") or "").strip()
        if not name or name in _RUNTIME_MANAGED_COLMAP_OPTIONS:
            continue
        option_schema = dict(option.get("schema") or {"type": "string"})
        description = option.get("description")
        if description and "description" not in option_schema:
            option_schema["description"] = description
        properties[name] = option_schema
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
    }


def _colmap_config_descriptors(backend: Any, *, include_schema: bool) -> list[dict[str, Any]]:
    schema_fn = getattr(backend, "colmap_command_schema", None)
    if not callable(schema_fn):
        return []
    capabilities = set()
    capabilities_fn = getattr(backend, "capabilities", None)
    if callable(capabilities_fn):
        try:
            capabilities = set(capabilities_fn())
        except Exception:
            capabilities = set()

    rows: list[dict[str, Any]] = []
    for config_id, stage, capability, provider, command in _COLMAP_STAGE_CONFIGS:
        if capabilities and capability not in capabilities:
            continue
        option_schema = None
        metadata: dict[str, Any] = {"family": "colmap", "command": command}
        if include_schema:
            try:
                native_schema = schema_fn(command)
            except Exception:
                continue
            metadata["native_schema"] = native_schema
            metadata["schema_source"] = native_schema.get("schema_source")
            metadata["option_count"] = native_schema.get(
                "option_count", len(native_schema.get("options") or [])
            )
            option_schema = _schema_for_colmap_backend_options(native_schema)
        rows.append(
            _normalize_descriptor(
                {
                    "config_id": config_id,
                    "backend": _backend_name(backend),
                    "stage": stage,
                    "capability": capability,
                    "provider": provider,
                    "display_name": f"COLMAP {stage} options",
                    "description": (
                        f"Backend-specific COLMAP `{command}` options accepted through "
                        f"`backend_options` for `{capability}`."
                    ),
                    "option_schema": option_schema,
                    "metadata": metadata,
                },
                backend=backend,
                include_schema=include_schema,
            )
        )
    return rows


_RADIANCE_TRAIN_CONFIG_ID = "radiance.train"


def _radiance_train_option_schema() -> dict[str, Any]:
    """Canonical cross-engine radiance/3DGS training knobs.

    ``max_steps`` and ``eval`` are first-class typed fields on
    ``RadianceTrainRequest``; these are the remaining splat-universal knobs
    carried through ``backend_options``. Each plugin maps the canonical name to
    its native option (``num_gaussians`` -> max_splats/max_cap/max_primitives/
    model.cap_max). This describes the CANONICAL (closed) knob set only --
    ``additionalProperties: false`` is the config-schema contract requirement;
    genuinely engine-specific extras still flow through the open
    ``backend_options`` envelope (radiance training does not strict-validate
    backend_options against this schema -- it is for discovery/portability).
    """
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "num_gaussians": {
                "type": "integer",
                "minimum": 1,
                "description": "Gaussian/primitive cap (mapped to each engine's native cap option).",
            },
            "max_resolution": {
                "type": "integer",
                "minimum": 1,
                "description": (
                    "Max training image dimension in px. Engines that train full-resolution "
                    "or take a scale factor (e.g. fastergs image_scale_factor) document the "
                    "deviation."
                ),
            },
            "init": {
                "type": "string",
                "enum": ["colmap", "random"],
                "default": "colmap",
                "description": "Gaussian initialization source.",
            },
            "test_every": {
                "type": "integer",
                "minimum": 1,
                "default": 8,
                "description": "Hold out every Nth registered view as the evaluation split.",
            },
        },
    }


def _radiance_config_descriptors(backend: Any, *, include_schema: bool) -> list[dict[str, Any]]:
    """Framework-owned canonical ``radiance.train`` schema for any backend that
    advertises the ``radiance.train`` capability (single source of truth; the
    splatting plugins all map their native options to it). No-op for backends
    without the capability (e.g. the stub), so existing responses are unchanged.
    """
    capabilities_fn = getattr(backend, "capabilities", None)
    if not callable(capabilities_fn):
        return []
    try:
        capabilities = set(capabilities_fn())
    except Exception:
        return []
    if "radiance.train" not in capabilities:
        return []
    return [
        _normalize_descriptor(
            {
                "config_id": _RADIANCE_TRAIN_CONFIG_ID,
                "backend": _backend_name(backend),
                "stage": "radiance",
                "capability": "radiance.train",
                "display_name": "Radiance training options",
                "description": (
                    "Canonical cross-engine radiance/3DGS training knobs accepted through "
                    "`backend_options`. `max_steps` and `eval` are first-class "
                    "RadianceTrainRequest fields."
                ),
                "option_schema": _radiance_train_option_schema() if include_schema else None,
                # Defaults are also inlined in option_schema.properties[*].default
                # (e.g. test_every=8, init="colmap"); duplicate at the descriptor
                # level so the dedicated `defaults` field stops being empty.
                "defaults": {"init": "colmap", "test_every": 8},
                # Canonical -> native option mapping per known provider. The
                # canonical name is what the client should use; each plugin
                # back-fills its native flag from it (see _apply_canonical_options
                # in each splatting trainer). Engine-specific knobs not in the
                # canonical set still flow through `backend_options`.
                "metadata": {
                    "family": "radiance",
                    "native_aliases": {
                        "num_gaussians": {
                            "gsplat": "num_gaussians",
                            "brush": "max_splats",
                            "lfs": "max_cap",
                            "fastergs": "max_primitives",
                            "spirulae": "model.cap_max",
                        },
                        "max_resolution": {
                            "gsplat": "target_size",
                            "brush": "max_resolution",
                            "lfs": "max_width",
                            "fastergs": "image_scale_factor (scale, not max-dim)",
                            "spirulae": "n/a (trains full-resolution)",
                        },
                    },
                },
            },
            backend=backend,
            include_schema=include_schema,
        )
    ]


def list_backend_config_schemas(
    backend: Any | None = None,
    *,
    include_schemas: bool = True,
) -> list[dict[str, Any]]:
    """List normalized backend-specific option schemas."""
    backend = backend or get_backend()
    rows: list[dict[str, Any]] = []

    generic = getattr(backend, "list_backend_config_schemas", None)
    if callable(generic):
        for raw in _call_with_supported_kwargs(generic, include_schemas=include_schemas):
            rows.append(_normalize_descriptor(raw, backend=backend, include_schema=include_schemas))
        if rows:
            return _dedupe(rows)

    rows.extend(_colmap_config_descriptors(backend, include_schema=include_schemas))
    rows.extend(_radiance_config_descriptors(backend, include_schema=include_schemas))
    return _dedupe(rows)


def has_backend_config_schemas(backend: Any | None = None) -> bool:
    try:
        return bool(list_backend_config_schemas(backend, include_schemas=False))
    except Exception:
        return False


def get_backend_config_schema(config_id: str, backend: Any | None = None) -> dict[str, Any]:
    backend = backend or get_backend()
    for row in list_backend_config_schemas(backend, include_schemas=True):
        if row["config_id"] == config_id:
            return row
    raise NotFoundError(f"Backend config schema {config_id!r} not found")


def _json_type_matches(value: Any, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "string":
        return isinstance(value, str)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    return True


def _validate_against_schema(
    *,
    config_id: str,
    options: dict[str, Any],
    schema: dict[str, Any] | None,
) -> list[dict[str, str | None]]:
    if not schema:
        return []
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return []

    errors: list[dict[str, str | None]] = []
    if schema.get("additionalProperties") is False:
        for key in sorted(options):
            if key not in properties:
                errors.append(
                    {
                        "field": key,
                        "message": f"{key!r} is not a valid option for {config_id}",
                    }
                )

    for key, value in sorted(options.items()):
        option_schema = properties.get(key)
        if not isinstance(option_schema, dict):
            continue
        choices = option_schema.get("enum")
        if choices is not None and value not in choices:
            errors.append(
                {
                    "field": key,
                    "message": f"{key!r} must be one of {list(choices)!r}",
                }
            )
            continue
        raw_type = option_schema.get("type")
        if raw_type is None:
            continue
        expected_types = raw_type if isinstance(raw_type, list) else [raw_type]
        expected = [str(item) for item in expected_types]
        if not any(_json_type_matches(value, item) for item in expected):
            errors.append(
                {
                    "field": key,
                    "message": f"{key!r} expects JSON type {'/'.join(expected)}",
                }
            )
    return errors


def validate_against_schema(
    identifier: str,
    values: dict[str, Any],
    schema: dict[str, Any] | None,
) -> list[dict[str, str | None]]:
    """Validate a flat ``values`` dict against a JSON object ``schema``
    (unknown keys when ``additionalProperties`` is false, enum, basic
    types). Public entry point shared by config-option and backend-action
    ("json" kind) validation so both interpret a schema identically.
    """
    return _validate_against_schema(config_id=identifier, options=values, schema=schema)


def validate_backend_options(
    *,
    stage: str,
    options: dict[str, Any] | None,
    capability: str | None = None,
    provider: str | None = None,
    backend: Any | None = None,
) -> dict[str, Any]:
    """Validate one stage's ``backend_options`` if the backend exposes a schema.

    Backends may omit schemas; in that case sfmapi passes options
    through and the backend remains the source of truth. When a schema
    is available, sfmapi catches unknown keys and simple type mistakes
    before creating a job.
    """
    if options is None:
        options = {}
    if not isinstance(options, dict):
        raise ValidationError(f"{stage}.backend_options must be an object")
    if not options:
        return {"valid": True, "errors": [], "normalized_options": {}}

    if backend is None:
        try:
            if provider is not None:
                ensure_provider_enabled(provider)
            backend = get_backend(provider=provider)
        except KeyError as exc:
            raise ValidationError(str(exc)) from exc
    rows = list_backend_config_schemas(backend, include_schemas=True)
    stage_rows = [row for row in rows if row.get("stage") == stage]
    if capability:
        exact = [row for row in stage_rows if row.get("capability") == capability]
        if exact:
            stage_rows = exact
    if provider:
        backend_name = str(getattr(backend, "name", "unknown"))
        provider_rows = [
            row
            for row in stage_rows
            if row.get("provider") in {provider, backend_name}
            or row.get("backend") in {provider, backend_name}
        ]
        if not provider_rows:
            return {"valid": True, "errors": [], "normalized_options": dict(options)}
        stage_rows = provider_rows
    if not stage_rows:
        return {"valid": True, "errors": [], "normalized_options": dict(options)}

    candidate_errors: list[tuple[str, list[dict[str, str | None]]]] = []
    for row in stage_rows:
        config_id = str(row["config_id"])
        errors = _validate_against_schema(
            config_id=config_id,
            options=options,
            schema=row.get("option_schema"),
        )
        if not errors:
            return {"valid": True, "errors": [], "normalized_options": dict(options)}
        candidate_errors.append((config_id, errors))

    config_id, errors = min(candidate_errors, key=lambda item: len(item[1]))
    detail = "; ".join(str(error.get("message")) for error in errors)
    raise ValidationError(detail or f"invalid backend_options for {config_id}")


def backend_config_contract_violations(backend: Any) -> list[str]:
    """Return backend config-schema contract violations for backend authors."""
    errors: list[str] = []
    try:
        rows = list_backend_config_schemas(backend, include_schemas=True)
    except Exception as exc:
        return [f"list_backend_config_schemas() failed: {exc}"]

    config_ids: list[str] = []
    for index, row in enumerate(rows):
        config_id = str(row.get("config_id") or "")
        label = config_id or f"config[{index}]"
        if not config_id:
            errors.append(f"{label}: config_id is required")
            continue
        config_ids.append(config_id)
        if not _NAMESPACED_ID_RE.match(config_id):
            errors.append(f"{label}: config_id should be namespaced, e.g. vendor.stage")
        stage = str(row.get("stage") or "").strip()
        if not stage:
            errors.append(f"{label}: stage is required")
        elif stage not in _VALID_STAGES:
            errors.append(f"{label}: stage must be one of {sorted(_VALID_STAGES)}")
        provider = row.get("provider")
        if provider is not None and not _PROVIDER_RE.match(str(provider)):
            errors.append(f"{label}: provider must match /^[A-Za-z0-9][A-Za-z0-9_.-]*$/")
        capability = row.get("capability")
        if capability is not None and str(capability) not in ALL_KNOWN:
            errors.append(f"{label}: capability {capability!r} is not portable")
        option_schema = row.get("option_schema")
        if option_schema is not None and not isinstance(option_schema, dict):
            errors.append(f"{label}: option_schema must be an object or null")
        elif isinstance(option_schema, dict):
            if option_schema.get("type") not in (None, "object"):
                errors.append(f"{label}: option_schema.type must be object")
            if option_schema.get("additionalProperties") is not False:
                errors.append(
                    f"{label}: option_schema.additionalProperties must be false "
                    "so sfmapi can reject misspelled backend_options"
                )
            properties = option_schema.get("properties", {})
            if properties is not None and not isinstance(properties, dict):
                errors.append(f"{label}: option_schema.properties must be an object")
            elif isinstance(properties, dict):
                for name, property_schema in properties.items():
                    option_name = str(name)
                    if option_name in _RUNTIME_MANAGED_COLMAP_OPTIONS:
                        errors.append(
                            f"{label}: option_schema must not expose runtime-managed "
                            f"option {option_name!r}; sfmapi supplies it"
                        )
                    if not isinstance(property_schema, dict):
                        errors.append(
                            f"{label}: option_schema.properties.{option_name} must be an object"
                        )
                required = option_schema.get("required", [])
                if required is not None and not isinstance(required, list):
                    errors.append(f"{label}: option_schema.required must be an array")
                elif isinstance(required, list):
                    for name in required:
                        option_name = str(name)
                        if option_name not in properties:
                            errors.append(
                                f"{label}: option_schema.required contains unknown "
                                f"property {option_name!r}"
                            )
                        if option_name in _RUNTIME_MANAGED_COLMAP_OPTIONS:
                            errors.append(
                                f"{label}: option_schema.required must not include "
                                f"runtime-managed option {option_name!r}"
                            )

    duplicates = sorted({config_id for config_id in config_ids if config_ids.count(config_id) > 1})
    for config_id in duplicates:
        errors.append(f"{config_id}: duplicate config_id")
    return errors


def assert_backend_config_contract(backend: Any) -> None:
    """Raise ``AssertionError`` if backend option schemas are malformed."""

    violations = backend_config_contract_violations(backend)
    if violations:
        raise AssertionError(
            "Backend config schema contract violations:\n"
            + "\n".join(f"- {violation}" for violation in violations)
        )


__all__ = [
    "BackendConfigSchemaProvider",
    "assert_backend_config_contract",
    "backend_config_contract_violations",
    "get_backend_config_schema",
    "has_backend_config_schemas",
    "list_backend_config_schemas",
    "validate_backend_options",
]
