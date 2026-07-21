"""Shared stage-artifact vocabulary and validation helpers.

The format-id VOCABULARY (ids, the DataType each format serializes, and
the descriptions) is owned by the contract plane
(:data:`sceneio.formats.CORE_FORMATS`); this module joins that
registry with the core-side wire details that stay here (titles,
``media_types`` tuples, manifest JSON schemas, examples) into the
:class:`ArtifactFormatDefinition` rows the routes serve. Every id and
served field is byte-identical to the pre-re-home literals — wire
identity is untouched; drift between the two sides fails loudly at
import.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sceneio.formats import CORE_FORMATS as _IO_CORE_FORMATS

# The artifact-key pattern is owned by sceneapi.server.core.ids (one home per id
# class); re-exported here so callers keep using artifacts.ARTIFACT_KEY_RE.
from sceneapi.server.core.datatypes import CORE_DATA_TYPES
from sceneapi.server.core.ids import ARTIFACT_KEY_RE

# The logical-type axis is owned by sceneapi.server.core.datatypes. An artifact's
# ``datatype`` IS its DataType id -- there is no separate artifact-type
# vocabulary and no bridge between the two. CORE_ARTIFACT_TYPES is exactly the
# artifact-kind DataTypes (scene inputs are not artifacts), derived from the
# DataType registry so the two cannot drift; the datatype-io completeness gate
# asserts every one is realized by a format and every format names a real type.
CORE_ARTIFACT_TYPES: frozenset[str] = frozenset(
    t.type_id for t in CORE_DATA_TYPES if t.kind == "artifact"
)


@dataclass(frozen=True)
class ArtifactKindDefinition:
    kind: str
    datatype: str  # the DataType id this kind is an instance of
    title: str
    description: str
    durable: bool
    artifact_format: str
    schema_version: int


@dataclass(frozen=True)
class ArtifactFormatDefinition:
    format_id: str
    datatype: str  # the DataType id this format serializes
    title: str
    description: str
    schema_version: int
    media_types: tuple[str, ...]
    json_schema: dict[str, Any] | None = None
    examples: tuple[dict[str, Any], ...] = ()
    portable: bool = True
    # Optional: the specific kinds this format serializes. Empty = the whole
    # DataType (every kind of ``datatype``). A plugin uses this to override
    # the I/O of ONE kind (e.g. only ``features.global.v1``) without disturbing
    # the sibling kinds of the same DataType -- kind-addressable I/O.
    serves_kinds: tuple[str, ...] = ()


def _manifest_schema(
    format_id: str, datatype: str, *, required: tuple[str, ...] = ()
) -> dict[str, Any]:
    base_required = ["format_id", "schema_version", *required]
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"https://sfmapi.github.io/schemas/artifacts/{format_id}.schema.json",
        "type": "object",
        "required": base_required,
        "properties": {
            "format_id": {"const": format_id},
            "schema_version": {"const": 1},
            "datatype": {"const": datatype},
            "coordinate_frame": {"type": "string"},
            "producer": {"type": "object", "additionalProperties": True},
            "files": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["name", "uri"],
                    "properties": {
                        "name": {"type": "string", "minLength": 1},
                        "uri": {"type": "string", "minLength": 1},
                        "media_type": {"type": "string"},
                        "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                        "byte_size": {"type": "integer", "minimum": 0},
                    },
                    "additionalProperties": True,
                },
            },
        },
        "additionalProperties": True,
    }


@dataclass(frozen=True)
class _CoreFormatWireDetails:
    """The core-side half of one format row.

    The id / datatype / description half lives in
    :data:`sceneio.formats.CORE_FORMATS` (``FormatSpec.kind`` IS the
    artifact datatype); the wire details below stay core-side per the
    Step-1 report and are joined with the contract-plane spec at import.
    """

    title: str
    media_types: tuple[str, ...]
    schema_required: tuple[str, ...]
    examples: tuple[dict[str, Any], ...] = ()


_CORE_FORMAT_WIRE_DETAILS: dict[str, _CoreFormatWireDetails] = {
    "sfmapi.features.local.v1": _CoreFormatWireDetails(
        title="sfmapi local features",
        media_types=("application/json", "application/x-ndjson", "application/octet-stream"),
        schema_required=("images",),
        examples=(
            {
                "format_id": "sfmapi.features.local.v1",
                "schema_version": 1,
                "datatype": "feature_set",
                "descriptor": {"type": "sift", "dtype": "float32", "dimension": 128},
                "images": [{"name": "image0001.jpg", "keypoints": "features/image0001.kpt.npy"}],
            },
        ),
    ),
    "sfmapi.features.global.v1": _CoreFormatWireDetails(
        title="sfmapi global descriptors",
        media_types=("application/json", "application/octet-stream"),
        schema_required=("images",),
        examples=(
            {
                "format_id": "sfmapi.features.global.v1",
                "schema_version": 1,
                "datatype": "feature_set",
                "descriptor": {"type": "vlad", "dtype": "float32", "dimension": 4096},
                "images": [{"name": "image0001.jpg", "descriptor": "global/image0001.npy"}],
            },
        ),
    ),
    "sfmapi.pairs.image_names.v1": _CoreFormatWireDetails(
        title="sfmapi image-name pairs",
        media_types=("text/plain", "application/json"),
        schema_required=("pairs",),
        examples=(
            {
                "format_id": "sfmapi.pairs.image_names.v1",
                "schema_version": 1,
                "datatype": "pair_set",
                "pairs": [["image0001.jpg", "image0002.jpg"]],
            },
        ),
    ),
    "sfmapi.matches.indexed.v1": _CoreFormatWireDetails(
        title="sfmapi indexed matches",
        media_types=("application/json", "application/octet-stream"),
        schema_required=("pairs",),
        examples=(
            {
                "format_id": "sfmapi.matches.indexed.v1",
                "schema_version": 1,
                "datatype": "match_graph",
                "pairs": [{"image1": "a.jpg", "image2": "b.jpg", "matches": [[0, 4], [8, 9]]}],
            },
        ),
    ),
    "sfmapi.matches.coordinates.v1": _CoreFormatWireDetails(
        title="sfmapi coordinate matches",
        media_types=("application/json", "application/octet-stream"),
        schema_required=("pairs",),
    ),
    "sfmapi.matches.dense.v1": _CoreFormatWireDetails(
        title="sfmapi dense matches",
        media_types=("application/json", "application/octet-stream"),
        schema_required=("tiles",),
    ),
    "sfmapi.matches.verified.v1": _CoreFormatWireDetails(
        title="sfmapi verified two-view geometry",
        media_types=("application/json", "application/octet-stream"),
        schema_required=("pairs",),
        examples=(
            {
                "format_id": "sfmapi.matches.verified.v1",
                "schema_version": 1,
                "datatype": "match_graph",
                "pairs": [
                    {
                        "image1": "a.jpg",
                        "image2": "b.jpg",
                        "num_inliers": 128,
                        "E": [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0],
                    }
                ],
            },
        ),
    ),
    "sfmapi.reconstruction.sparse.v1": _CoreFormatWireDetails(
        title="sfmapi sparse reconstruction",
        media_types=("application/json", "application/octet-stream"),
        schema_required=("files",),
    ),
    "sfmapi.reconstruction.snapshot.v1": _CoreFormatWireDetails(
        title="sfmapi sealed snapshot",
        media_types=("application/json", "application/octet-stream"),
        schema_required=("files",),
    ),
    "sfmapi.reconstruction.submodel.v1": _CoreFormatWireDetails(
        title="sfmapi reconstruction submodel",
        media_types=("application/json", "application/octet-stream"),
        schema_required=("files",),
    ),
    "sfmapi.projection.images.v1": _CoreFormatWireDetails(
        title="sfmapi projected image set",
        media_types=("application/json", "image/jpeg", "image/png"),
        schema_required=("source_images", "output_images"),
    ),
}


def _build_core_artifact_formats() -> dict[str, ArtifactFormatDefinition]:
    """Join the contract-plane format registry with the core wire details.

    Wire identity is the contract: the joined table must cover exactly
    the sceneio ids (no extras, no gaps) — a mismatch is a packaging
    error and fails loudly at import instead of silently drifting.
    """
    io_ids = set(_IO_CORE_FORMATS)
    core_ids = set(_CORE_FORMAT_WIRE_DETAILS)
    if io_ids != core_ids:
        raise ValueError(
            f"core artifact formats out of sync with sceneio.formats.CORE_FORMATS: "
            f"missing core-side details for {sorted(io_ids - core_ids)}; "
            f"core-side details with no contract-plane id {sorted(core_ids - io_ids)}"
        )
    out: dict[str, ArtifactFormatDefinition] = {}
    for format_id, spec in _IO_CORE_FORMATS.items():
        details = _CORE_FORMAT_WIRE_DETAILS[format_id]
        out[format_id] = ArtifactFormatDefinition(
            format_id=spec.id,
            datatype=spec.kind,
            title=details.title,
            description=spec.description,
            schema_version=1,
            media_types=details.media_types,
            json_schema=_manifest_schema(spec.id, spec.kind, required=details.schema_required),
            examples=details.examples,
        )
    return out


CORE_ARTIFACT_FORMATS: dict[str, ArtifactFormatDefinition] = _build_core_artifact_formats()


def resolve_io_formats(
    type_id: str,
    *,
    plugin_formats: tuple[ArtifactFormatDefinition, ...] = (),
) -> list[ArtifactFormatDefinition]:
    """Resolve the formats that read/write a DataType, plugin overrides first.

    The Format axis is OPEN: a plugin may provide its own serialization for a
    core DataType. A format serializes exactly its ``datatype`` (== a
    DataType id); plugin-provided formats of ``type_id`` take precedence, the
    core portable formats follow as the interchange fallback. The completeness
    gate guarantees a core format always exists, so this never returns empty for
    a known artifact DataType -- a plugin can override the I/O, never remove it.
    """
    plugin = [f for f in plugin_formats if f.datatype == type_id]
    core = [f for f in CORE_ARTIFACT_FORMATS.values() if f.datatype == type_id]
    return [*plugin, *core]


def _kind(
    kind: str,
    title: str,
    description: str,
    *,
    durable: bool,
    artifact_format: str,
) -> ArtifactKindDefinition:
    """One core kind row; its ``datatype`` is derived from the format it
    defaults to (which mirrors ``FormatSpec.kind`` in the contract-plane
    registry) — the kind table cannot drift from the format vocabulary."""
    return ArtifactKindDefinition(
        kind=kind,
        datatype=CORE_ARTIFACT_FORMATS[artifact_format].datatype,
        title=title,
        description=description,
        durable=durable,
        artifact_format=artifact_format,
        schema_version=1,
    )


CORE_ARTIFACT_KINDS: dict[str, ArtifactKindDefinition] = {
    "features.local.v1": _kind(
        "features.local.v1",
        "Local feature set",
        (
            "Portable per-image local keypoints and descriptors. Supports SIFT-like "
            "and learned local features through manifest-declared layouts."
        ),
        durable=False,
        artifact_format="sfmapi.features.local.v1",
    ),
    "features.global.v1": _kind(
        "features.global.v1",
        "Global image descriptors",
        "Portable per-image retrieval descriptors such as VLAD or NetVLAD.",
        durable=False,
        artifact_format="sfmapi.features.global.v1",
    ),
    "pairs.image_names.v1": _kind(
        "pairs.image_names.v1",
        "Image-name pairs",
        "Portable newline-delimited or manifest-addressed image pair list.",
        durable=False,
        artifact_format="sfmapi.pairs.image_names.v1",
    ),
    "matches.indexed.v1": _kind(
        "matches.indexed.v1",
        "Indexed feature matches",
        "Portable raw matches expressed as keypoint-index pairs.",
        durable=False,
        artifact_format="sfmapi.matches.indexed.v1",
    ),
    "matches.coordinates.v1": _kind(
        "matches.coordinates.v1",
        "Coordinate matches",
        "Portable detector-free matches expressed as image coordinate pairs.",
        durable=False,
        artifact_format="sfmapi.matches.coordinates.v1",
    ),
    "matches.dense.v1": _kind(
        "matches.dense.v1",
        "Dense or semi-dense matches",
        "Portable tiled/chunked dense match field.",
        durable=False,
        artifact_format="sfmapi.matches.dense.v1",
    ),
    "matches.verified.v1": _kind(
        "matches.verified.v1",
        "Verified two-view geometry",
        "Portable verified matches with F/E/H matrices and inlier pairs.",
        durable=False,
        artifact_format="sfmapi.matches.verified.v1",
    ),
    "reconstruction.sparse.v1": _kind(
        "reconstruction.sparse.v1",
        "Sparse reconstruction",
        "Portable cameras, image poses, tracks, and sparse points manifest.",
        durable=True,
        artifact_format="sfmapi.reconstruction.sparse.v1",
    ),
    "reconstruction.snapshot": _kind(
        "reconstruction.snapshot",
        "Sealed reconstruction snapshot",
        "Immutable sealed snapshot directory for a reconstruction.",
        durable=True,
        artifact_format="sfmapi.reconstruction.snapshot.v1",
    ),
    "reconstruction.submodel": _kind(
        "reconstruction.submodel",
        "Reconstruction submodel",
        "One disconnected mapping component inside a reconstruction snapshot.",
        durable=True,
        artifact_format="sfmapi.reconstruction.submodel.v1",
    ),
    "projection.images.v1": _kind(
        "projection.images.v1",
        "Projected image set",
        "Images produced by a portable projection transform plus a manifest.",
        durable=True,
        artifact_format="sfmapi.projection.images.v1",
    ),
}

# A kind id's namespace (its first segment, e.g. ``features`` in
# ``features.local.v1``) is a naming convention, distinct from the type axis.
# This maps that namespace to its DataType, LEARNED from the core kinds -- so a
# backend extension kind (``features.hloc_h5``) inherits the DataType of its
# namespace without a separately-maintained bridge table.
#
# Guarded against silent ambiguity: a namespace must map to exactly ONE
# DataType. When dense_model/splat land (deferred), a ``reconstruction.dense.*``
# kind of a DIFFERENT DataType than ``reconstruction.sparse.*`` would make
# namespace inference ambiguous -- this fails loudly at import so the decision
# (split the namespace, or make extension inference explicit) is deliberate,
# not a last-wins surprise.
_KIND_NAMESPACE_TO_DATATYPE: dict[str, str] = {}
for _kind, _kind_def in CORE_ARTIFACT_KINDS.items():
    _ns = _kind.split(".", 1)[0]
    if _KIND_NAMESPACE_TO_DATATYPE.get(_ns, _kind_def.datatype) != _kind_def.datatype:
        raise ValueError(
            f"kind namespace {_ns!r} maps to two DataTypes "
            f"({_KIND_NAMESPACE_TO_DATATYPE[_ns]!r}, {_kind_def.datatype!r}); "
            f"extension-kind inference can no longer use the namespace -- split "
            f"the namespace or make the type explicit"
        )
    _KIND_NAMESPACE_TO_DATATYPE[_ns] = _kind_def.datatype

ARTIFACT_INPUT_ROLE_KINDS: dict[str, frozenset[str]] = {
    "features": frozenset({"features.local.v1", "features.global.v1"}),
    "pairs": frozenset({"pairs.image_names.v1"}),
    "matches": frozenset(
        {
            "matches.indexed.v1",
            "matches.coordinates.v1",
            "matches.dense.v1",
        }
    ),
    "verified_matches": frozenset(
        {
            "matches.verified.v1",
        }
    ),
    "snapshot": frozenset({"reconstruction.sparse.v1", "reconstruction.snapshot"}),
    "submodel": frozenset({"reconstruction.submodel"}),
}

ARTIFACT_INPUT_ROLE_PREFIXES: dict[str, tuple[str, ...]] = {
    "features": ("features.",),
    "pairs": ("pairs.",),
    "matches": ("matches.",),
    "verified_matches": ("matches.verified.", "matches.database.verified."),
    "snapshot": ("reconstruction.sparse.", "reconstruction.snapshot."),
    "submodel": ("reconstruction.submodel.",),
}


def is_valid_artifact_key(value: str) -> bool:
    return bool(ARTIFACT_KEY_RE.fullmatch(value))


def is_core_artifact_kind(value: str) -> bool:
    return value in CORE_ARTIFACT_KINDS


def is_core_artifact_format(value: str) -> bool:
    return value in CORE_ARTIFACT_FORMATS


def default_format_for_kind(kind: str) -> ArtifactFormatDefinition | None:
    kind_def = CORE_ARTIFACT_KINDS.get(kind)
    if kind_def is None:
        return None
    return CORE_ARTIFACT_FORMATS[kind_def.artifact_format]


def kind_for_default_format(format_id: str) -> str | None:
    for kind, kind_def in CORE_ARTIFACT_KINDS.items():
        if kind_def.artifact_format == format_id:
            return kind
    return None


def datatype_for_format(format_id: str) -> str | None:
    format_def = CORE_ARTIFACT_FORMATS.get(format_id)
    if format_def is None:
        return None
    return format_def.datatype


def datatype_for_kind(kind: str) -> str | None:
    kind_def = CORE_ARTIFACT_KINDS.get(kind)
    if kind_def is not None:
        return kind_def.datatype
    namespace = kind.split(".", 1)[0]
    return _KIND_NAMESPACE_TO_DATATYPE.get(namespace)


def is_format_compatible_with_kind(kind: str, format_id: str) -> bool:
    kind_datatype = datatype_for_kind(kind)
    format_datatype = datatype_for_format(format_id)
    if kind_datatype is None or format_datatype is None:
        return True
    return kind_datatype == format_datatype


def is_artifact_allowed_for_role(role: str, kind: str) -> bool:
    """Return whether an artifact kind is semantically compatible with a role.

    Core portable kinds are checked exactly. Backend-native extension
    kinds remain usable when they stay in the role's accepted namespace,
    for example ``features.hloc_h5`` for ``features`` or
    ``matches.database.verified.colmap`` for ``verified_matches``.
    """
    allowed = ARTIFACT_INPUT_ROLE_KINDS.get(role)
    if allowed is None:
        return True
    if kind in allowed:
        return True
    return any(kind.startswith(prefix) for prefix in ARTIFACT_INPUT_ROLE_PREFIXES.get(role, ()))
