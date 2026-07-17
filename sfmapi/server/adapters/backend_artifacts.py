"""Backend artifact input/output contract discovery."""

from __future__ import annotations

from typing import Any, Protocol
from urllib.parse import quote

from sfmapi.server.adapters.backend import has_backend_method
from sfmapi.server.adapters.registry import get_backend
from sfmapi.server.adapters.stub_backend import StubBackend
from sfmapi.server.core import artifacts as artifact_vocab
from sfmapi.server.core.capabilities import ALL_KNOWN
from sfmapi.server.core.errors import NotFoundError, ValidationError


class BackendArtifactContractProvider(Protocol):
    """Optional structural protocol for backends with explicit artifact I/O."""

    def list_backend_artifact_contracts(self) -> list[dict[str, Any]]: ...


# The config-stage vocabulary is core-owned (sfmapi.server.core.config_stages); the
# adapter keeps the local underscore names for its existing call sites.
from sfmapi.server.core.config_stages import CONFIG_STAGE_ORDER as _STAGE_ORDER  # noqa: E402
from sfmapi.server.core.config_stages import VALID_CONFIG_STAGES as _VALID_STAGES  # noqa: E402

# Canonical patterns live in sfmapi.server.core.ids; re-export under the local
# underscore names to keep existing call sites in this file untouched.
from sfmapi.server.core.ids import NAMESPACED_ID_RE as _NAMESPACED_ID_RE  # noqa: E402
from sfmapi.server.core.ids import PROVIDER_ID_RE as _PROVIDER_RE  # noqa: E402


def _backend_name(backend: Any) -> str:
    return str(getattr(backend, "name", "unknown"))


def _link(contract_id: str) -> dict[str, dict[str, str]]:
    encoded = quote(contract_id, safe="")
    return {
        "self": {"href": f"/v1/backend/artifact-contracts/{encoded}"},
        "collection": {"href": "/v1/backend/artifact-contracts"},
    }


def _list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValidationError("artifact contract accepts/emits/preferred values must be lists")
    return [str(item) for item in value]


def _formats_for_kinds(kinds: list[str]) -> list[str]:
    formats: list[str] = []
    for kind in kinds:
        format_def = artifact_vocab.default_format_for_kind(kind)
        if format_def is not None and format_def.format_id not in formats:
            formats.append(format_def.format_id)
    return formats


def _normalize_descriptor(raw: dict[str, Any], *, backend: Any) -> dict[str, Any]:
    contract_id = str(raw.get("contract_id") or raw.get("id") or raw.get("name") or "").strip()
    if not contract_id:
        raise ValidationError("backend artifact contract descriptor missing contract_id")
    capability = raw.get("capability")
    capability = None if capability is None else str(capability)
    provider = raw.get("provider")
    provider = None if provider is None else str(provider)
    accepts = _list(raw.get("accepts"))
    emits = _list(raw.get("emits"))
    accepts_formats = _list(raw.get("accepts_formats")) or _formats_for_kinds(accepts)
    emits_formats = _list(raw.get("emits_formats")) or _formats_for_kinds(emits)
    preferred = raw.get("preferred")
    preferred = None if preferred is None else str(preferred)
    preferred_format = raw.get("preferred_format")
    if preferred_format is None and preferred is not None:
        preferred_def = artifact_vocab.default_format_for_kind(preferred)
        preferred_format = preferred_def.format_id if preferred_def is not None else None
    preferred_format = None if preferred_format is None else str(preferred_format)
    return {
        "contract_id": contract_id,
        "backend": str(raw.get("backend") or _backend_name(backend)),
        "stage": str(raw.get("stage") or "other"),
        "capability": capability,
        "provider": provider,
        "display_name": raw.get("display_name") or raw.get("title") or contract_id,
        "description": raw.get("description"),
        "accepts": accepts,
        "emits": emits,
        "accepts_formats": accepts_formats,
        "emits_formats": emits_formats,
        "preferred": preferred,
        "preferred_format": preferred_format,
        "conversions": list(raw.get("conversions") or []),
        "metadata": dict(raw.get("metadata") or {}),
        "_links": _link(contract_id),
    }


def _dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        by_id.setdefault(str(row["contract_id"]), row)
    return sorted(
        by_id.values(),
        key=lambda item: (_STAGE_ORDER.get(str(item.get("stage")), 999), str(item["contract_id"])),
    )


def _portable_contracts_from_capabilities(backend: Any) -> list[dict[str, Any]]:
    capabilities_fn = getattr(backend, "capabilities", None)
    if not callable(capabilities_fn):
        return []
    try:
        capabilities = {str(item) for item in capabilities_fn()}
    except Exception:
        return []

    backend_name = _backend_name(backend)
    rows: list[dict[str, Any]] = []
    for capability in sorted(capabilities):
        if capability.startswith("features.extract."):
            feature_type = capability.removeprefix("features.extract.")
            rows.append(
                {
                    "contract_id": f"{backend_name}.features.{feature_type}",
                    "stage": "features",
                    "capability": capability,
                    "provider": backend_name,
                    "display_name": f"{backend_name} {feature_type} feature outputs",
                    "accepts": [],
                    "emits": ["features.local.v1"],
                    "preferred": "features.local.v1",
                }
            )
        elif capability.startswith("pairs."):
            strategy = capability.removeprefix("pairs.")
            rows.append(
                {
                    "contract_id": f"{backend_name}.pairs.{strategy}",
                    "stage": "pairs",
                    "capability": capability,
                    "provider": backend_name,
                    "display_name": f"{backend_name} {strategy} pair outputs",
                    "accepts": ["features.global.v1"],
                    "emits": ["pairs.image_names.v1"],
                    "preferred": "pairs.image_names.v1",
                }
            )
        elif capability.startswith("matchers."):
            matcher = capability.removeprefix("matchers.")
            output = "matches.coordinates.v1" if matcher == "loftr" else "matches.indexed.v1"
            rows.append(
                {
                    "contract_id": f"{backend_name}.matcher.{matcher}",
                    "stage": "matcher",
                    "capability": capability,
                    "provider": backend_name,
                    "display_name": f"{backend_name} {matcher} match outputs",
                    "accepts": ["features.local.v1", "pairs.image_names.v1"],
                    "emits": [output],
                    "preferred": output,
                }
            )
        elif capability == "matches.verify":
            rows.append(
                {
                    "contract_id": f"{backend_name}.verify",
                    "stage": "verify",
                    "capability": capability,
                    "provider": backend_name,
                    "display_name": f"{backend_name} verified match outputs",
                    "accepts": [
                        "matches.indexed.v1",
                        "matches.coordinates.v1",
                        "matches.dense.v1",
                    ],
                    "emits": ["matches.verified.v1"],
                    "preferred": "matches.verified.v1",
                }
            )
        elif capability.startswith("map."):
            kind = capability.removeprefix("map.")
            rows.append(
                {
                    "contract_id": f"{backend_name}.mapping.{kind}",
                    "stage": "mapping",
                    "capability": capability,
                    "provider": backend_name,
                    "display_name": f"{backend_name} {kind} reconstruction outputs",
                    "accepts": ["matches.verified.v1"],
                    "emits": ["reconstruction.sparse.v1", "reconstruction.snapshot"],
                    "preferred": "reconstruction.sparse.v1",
                }
            )
    return [_normalize_descriptor(row, backend=backend) for row in rows]


def list_backend_artifact_contracts(backend: Any | None = None) -> list[dict[str, Any]]:
    backend = backend or get_backend()
    generic = getattr(backend, "list_backend_artifact_contracts", None)
    if callable(generic):
        rows = [_normalize_descriptor(raw, backend=backend) for raw in generic()]
        if rows:
            return _dedupe(rows)
    return _dedupe(_portable_contracts_from_capabilities(backend))


def has_backend_artifact_contracts(backend: Any | None = None) -> bool:
    try:
        return bool(list_backend_artifact_contracts(backend))
    except Exception:
        return False


def get_backend_artifact_contract(contract_id: str, backend: Any | None = None) -> dict[str, Any]:
    backend = backend or get_backend()
    for row in list_backend_artifact_contracts(backend):
        if row["contract_id"] == contract_id:
            return row
    raise NotFoundError(f"Backend artifact contract {contract_id!r} not found")


def backend_artifact_contract_violations(backend: Any) -> list[str]:
    errors: list[str] = []
    try:
        rows = list_backend_artifact_contracts(backend)
    except Exception as exc:
        return [f"list_backend_artifact_contracts() failed: {exc}"]
    format_datatypes = {
        format_id: definition.datatype
        for format_id, definition in artifact_vocab.CORE_ARTIFACT_FORMATS.items()
    }
    for definition in backend_io_formats(backend):
        if not definition.datatype:
            errors.append(f"{definition.format_id}: artifact format datatype is required")
            continue
        if definition.datatype not in artifact_vocab.CORE_ARTIFACT_TYPES:
            errors.append(
                f"{definition.format_id}: artifact format datatype "
                f"{definition.datatype!r} is not a known artifact Data Type"
            )
            continue
        format_datatypes[definition.format_id] = definition.datatype

    ids: list[str] = []
    saw_conversion = False
    for index, row in enumerate(rows):
        contract_id = str(row.get("contract_id") or "")
        label = contract_id or f"artifact_contract[{index}]"
        if not contract_id:
            errors.append(f"{label}: contract_id is required")
            continue
        ids.append(contract_id)
        if not _NAMESPACED_ID_RE.match(contract_id):
            errors.append(f"{label}: contract_id should be namespaced, e.g. vendor.stage")
        stage = str(row.get("stage") or "").strip()
        if stage not in _VALID_STAGES:
            errors.append(f"{label}: stage must be one of {sorted(_VALID_STAGES)}")
        provider = row.get("provider")
        if provider is not None and not _PROVIDER_RE.match(str(provider)):
            errors.append(f"{label}: provider must match /^[A-Za-z0-9][A-Za-z0-9_.-]*$/")
        capability = row.get("capability")
        if capability is not None and str(capability) not in ALL_KNOWN:
            errors.append(f"{label}: capability {capability!r} is not portable")
        for field in ("accepts", "emits"):
            values = row.get(field)
            if not isinstance(values, list):
                errors.append(f"{label}: {field} must be a list")
                continue
            for value in values:
                if not artifact_vocab.is_valid_artifact_key(str(value)):
                    errors.append(f"{label}: {field} contains invalid artifact kind {value!r}")
        preferred = row.get("preferred")
        if preferred is not None and preferred not in set(row.get("emits") or []):
            errors.append(f"{label}: preferred must be one of emits")
        for field in ("accepts_formats", "emits_formats"):
            values = row.get(field)
            if not isinstance(values, list):
                errors.append(f"{label}: {field} must be a list")
                continue
            for value in values:
                if not artifact_vocab.is_valid_artifact_key(str(value)):
                    errors.append(f"{label}: {field} contains invalid format id {value!r}")
                    continue
                raw_kinds = row.get("accepts" if field == "accepts_formats" else "emits")
                kinds = raw_kinds if isinstance(raw_kinds, list) else []
                incompatible = [
                    str(kind)
                    for kind in kinds
                    if (kind_datatype := artifact_vocab.datatype_for_kind(str(kind))) is not None
                    and format_datatypes.get(str(value)) is not None
                    and kind_datatype != format_datatypes[str(value)]
                ]
                if str(value) not in format_datatypes:
                    errors.append(f"{label}: {field} format {value!r} has no declared Data Type")
                    continue
                if incompatible:
                    errors.append(
                        f"{label}: {field} format {value!r} is not compatible with "
                        f"{'accepts' if field == 'accepts_formats' else 'emits'} "
                        f"artifact kind(s) {incompatible}"
                    )
        preferred_format = row.get("preferred_format")
        if preferred_format is not None and preferred_format not in set(
            row.get("emits_formats") or []
        ):
            errors.append(f"{label}: preferred_format must be one of emits_formats")
        conversions = row.get("conversions") or []
        if not isinstance(conversions, list):
            errors.append(f"{label}: conversions must be a list")
        for conversion_index, conversion in enumerate(conversions):
            saw_conversion = True
            if not isinstance(conversion, dict):
                errors.append(f"{label}: conversions[{conversion_index}] must be an object")
                continue
            for field in ("from_format", "to_format"):
                value = conversion.get(field)
                if not isinstance(value, str) or not artifact_vocab.is_valid_artifact_key(value):
                    errors.append(f"{label}: conversions[{conversion_index}].{field} is required")
    duplicates = sorted({contract_id for contract_id in ids if ids.count(contract_id) > 1})
    for contract_id in duplicates:
        errors.append(f"{contract_id}: duplicate contract_id")
    if saw_conversion:
        method = getattr(type(backend), "convert_artifact", None)
        if (
            not has_backend_method(backend, "convert_artifact")
            or method is StubBackend.convert_artifact
        ):
            errors.append(
                "convert_artifact() must be implemented by the backend when artifact "
                "contracts advertise conversions"
            )
    return errors


def assert_backend_artifact_contract(backend: Any) -> None:
    violations = backend_artifact_contract_violations(backend)
    if violations:
        raise AssertionError(
            "Backend artifact contract violations:\n"
            + "\n".join(f"- {violation}" for violation in violations)
        )


def _coerce_format_def(raw: Any) -> artifact_vocab.ArtifactFormatDefinition | None:
    if isinstance(raw, artifact_vocab.ArtifactFormatDefinition):
        return raw
    if isinstance(raw, dict) and raw.get("format_id"):
        return artifact_vocab.ArtifactFormatDefinition(
            format_id=str(raw["format_id"]),
            datatype=str(raw.get("datatype") or ""),
            title=str(raw.get("title") or raw["format_id"]),
            description=str(raw.get("description") or ""),
            schema_version=int(raw.get("schema_version") or 1),
            media_types=tuple(str(m) for m in (raw.get("media_types") or ())),
            serves_kinds=tuple(str(k) for k in (raw.get("serves_kinds") or ())),
        )
    return None


def backend_io_formats(
    backend: Any | None = None,
) -> tuple[artifact_vocab.ArtifactFormatDefinition, ...]:
    """The plugin (backend-declared) artifact formats, for I/O resolution.

    The Format axis is open: a backend may serialize a core DataType its own
    way. An explicit ``artifact_formats()`` method wins; otherwise format
    objects are derived from the backend's artifact contracts -- any emitted or
    accepted format id that is NOT a core format is a backend-owned
    serialization, realizing the DataType of the kinds it serves. These feed
    :func:`sfmapi.server.core.artifacts.resolve_io_formats` as the plugin overrides
    (plugin-first), so a backend can override the core I/O format, never remove
    it. Defensive: no configured backend -> no plugin formats (core defaults).
    """
    if backend is None:
        try:
            backend = get_backend()
        except Exception:
            return ()

    explicit = getattr(backend, "artifact_formats", None)
    if callable(explicit):
        try:
            defs = [_coerce_format_def(item) for item in explicit()]
        except Exception:
            defs = []
        chosen = tuple(d for d in defs if d is not None)
        if chosen:
            return chosen

    derived: dict[str, artifact_vocab.ArtifactFormatDefinition] = {}
    try:
        rows = list_backend_artifact_contracts(backend)
    except Exception:
        rows = []
    for row in rows:
        # Each format serves only the kinds of THIS DataType on its OWN side of
        # the contract (emits_formats <- emits kinds, accepts_formats <- accepts
        # kinds) -- so a row that accepts feature formats and emits match
        # formats derives one DataType per side instead of smearing the first
        # row-level DataType across both.
        for field, kind_field in (("emits_formats", "emits"), ("accepts_formats", "accepts")):
            side_datatypes = {
                datatype
                for kind in row.get(kind_field, [])
                if (datatype := artifact_vocab.datatype_for_kind(str(kind)))
                in artifact_vocab.CORE_ARTIFACT_TYPES
            }
            if len(side_datatypes) != 1:
                continue
            datatype = next(iter(side_datatypes))
            serves_kinds = tuple(
                str(k)
                for k in row.get(kind_field, [])
                if artifact_vocab.datatype_for_kind(str(k)) == datatype
            )
            for raw_format in row.get(field) or []:
                format_id = str(raw_format)
                if format_id in artifact_vocab.CORE_ARTIFACT_FORMATS or format_id in derived:
                    continue
                derived[format_id] = artifact_vocab.ArtifactFormatDefinition(
                    format_id=format_id,
                    datatype=datatype,
                    title=format_id,
                    description=f"Backend-declared {datatype} format.",
                    schema_version=1,
                    media_types=(),
                    serves_kinds=serves_kinds,
                )
    return tuple(derived.values())


def backend_default_format_for_kind(kind: str, backend: Any | None = None) -> str | None:
    """A backend-declared format overriding the core default for ``kind``, else
    None (keep the core default).

    Kind-addressable: a plugin format that explicitly ``serves_kinds`` this kind
    wins; otherwise a type-level plugin format (``serves_kinds`` empty = the
    whole DataType) is used; otherwise the core default holds. So a plugin can
    override one kind (e.g. ``features.global.v1``) without disturbing the
    sibling kinds of the same DataType. Plugin override, never removal -- returns
    None unless the backend genuinely owns a format for THIS kind, so the core
    fallback (guaranteed by the I/O completeness gate) always holds.
    """
    core_kind = artifact_vocab.CORE_ARTIFACT_KINDS.get(kind)
    if core_kind is None:
        return None
    # core_kind.datatype IS the DataType id (no bridge anymore).
    type_id = core_kind.datatype
    if type_id not in artifact_vocab.CORE_ARTIFACT_TYPES:
        return None
    resolved = artifact_vocab.resolve_io_formats(
        type_id, plugin_formats=backend_io_formats(backend)
    )
    overrides = [
        fmt
        for fmt in resolved  # plugin overrides come first
        if fmt.format_id not in artifact_vocab.CORE_ARTIFACT_FORMATS
    ]
    kind_specific = next((f for f in overrides if kind in f.serves_kinds), None)
    if kind_specific is not None:
        return kind_specific.format_id
    type_level = next((f for f in overrides if not f.serves_kinds), None)
    if type_level is not None:
        return type_level.format_id
    # Every plugin format targets OTHER kinds of this DataType -> core default.
    return None


__all__ = [
    "BackendArtifactContractProvider",
    "assert_backend_artifact_contract",
    "backend_artifact_contract_violations",
    "backend_default_format_for_kind",
    "backend_io_formats",
    "get_backend_artifact_contract",
    "has_backend_artifact_contracts",
    "list_backend_artifact_contracts",
]
