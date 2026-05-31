"""Shared stage-artifact vocabulary and validation helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# The artifact-key pattern is owned by app.core.ids (one home per id
# class); re-exported here so callers keep using artifacts.ARTIFACT_KEY_RE.
from app.core.datatypes import CORE_DATA_TYPES
from app.core.ids import ARTIFACT_KEY_RE

# The logical-type axis is owned by app.core.datatypes. An artifact's
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
                        "sha256": {"type": "string", "pattern": "^[a-fA-F0-9]{64}$"},
                        "byte_size": {"type": "integer", "minimum": 0},
                    },
                    "additionalProperties": True,
                },
            },
        },
        "additionalProperties": True,
    }


CORE_ARTIFACT_FORMATS: dict[str, ArtifactFormatDefinition] = {
    "sfmapi.features.local.v1": ArtifactFormatDefinition(
        format_id="sfmapi.features.local.v1",
        datatype="feature_set",
        title="sfmapi local features",
        description=(
            "Versioned interchange manifest for per-image keypoints, descriptors, "
            "descriptor dtype/layout, and detector metadata."
        ),
        schema_version=1,
        media_types=("application/json", "application/x-ndjson", "application/octet-stream"),
        json_schema=_manifest_schema("sfmapi.features.local.v1", "feature_set", required=("images",)),
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
    "sfmapi.features.global.v1": ArtifactFormatDefinition(
        format_id="sfmapi.features.global.v1",
        datatype="feature_set",
        title="sfmapi global descriptors",
        description="Versioned interchange manifest for per-image retrieval descriptors.",
        schema_version=1,
        media_types=("application/json", "application/octet-stream"),
        json_schema=_manifest_schema(
            "sfmapi.features.global.v1",
            "feature_set",
            required=("images",),
        ),
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
    "sfmapi.pairs.image_names.v1": ArtifactFormatDefinition(
        format_id="sfmapi.pairs.image_names.v1",
        datatype="pair_set",
        title="sfmapi image-name pairs",
        description="Portable image-pair list keyed by dataset image names.",
        schema_version=1,
        media_types=("text/plain", "application/json"),
        json_schema=_manifest_schema(
            "sfmapi.pairs.image_names.v1",
            "pair_set",
            required=("pairs",),
        ),
        examples=(
            {
                "format_id": "sfmapi.pairs.image_names.v1",
                "schema_version": 1,
                "datatype": "pair_set",
                "pairs": [["image0001.jpg", "image0002.jpg"]],
            },
        ),
    ),
    "sfmapi.matches.indexed.v1": ArtifactFormatDefinition(
        format_id="sfmapi.matches.indexed.v1",
        datatype="match_graph",
        title="sfmapi indexed matches",
        description="Portable match graph expressed as feature-index pairs.",
        schema_version=1,
        media_types=("application/json", "application/octet-stream"),
        json_schema=_manifest_schema(
            "sfmapi.matches.indexed.v1",
            "match_graph",
            required=("pairs",),
        ),
        examples=(
            {
                "format_id": "sfmapi.matches.indexed.v1",
                "schema_version": 1,
                "datatype": "match_graph",
                "pairs": [{"image1": "a.jpg", "image2": "b.jpg", "matches": [[0, 4], [8, 9]]}],
            },
        ),
    ),
    "sfmapi.matches.coordinates.v1": ArtifactFormatDefinition(
        format_id="sfmapi.matches.coordinates.v1",
        datatype="match_graph",
        title="sfmapi coordinate matches",
        description="Portable detector-free match graph expressed as image coordinates.",
        schema_version=1,
        media_types=("application/json", "application/octet-stream"),
        json_schema=_manifest_schema(
            "sfmapi.matches.coordinates.v1",
            "match_graph",
            required=("pairs",),
        ),
    ),
    "sfmapi.matches.dense.v1": ArtifactFormatDefinition(
        format_id="sfmapi.matches.dense.v1",
        datatype="match_graph",
        title="sfmapi dense matches",
        description="Portable tiled dense or semi-dense correspondence field.",
        schema_version=1,
        media_types=("application/json", "application/octet-stream"),
        json_schema=_manifest_schema(
            "sfmapi.matches.dense.v1",
            "match_graph",
            required=("tiles",),
        ),
    ),
    "sfmapi.matches.verified.v1": ArtifactFormatDefinition(
        format_id="sfmapi.matches.verified.v1",
        datatype="match_graph",
        title="sfmapi verified two-view geometry",
        description="Portable verified correspondences with F/E/H matrices and inliers.",
        schema_version=1,
        media_types=("application/json", "application/octet-stream"),
        json_schema=_manifest_schema(
            "sfmapi.matches.verified.v1",
            "match_graph",
            required=("pairs",),
        ),
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
    "sfmapi.reconstruction.sparse.v1": ArtifactFormatDefinition(
        format_id="sfmapi.reconstruction.sparse.v1",
        datatype="sparse_model",
        title="sfmapi sparse reconstruction",
        description="Portable cameras, image poses, rigs, tracks, and sparse points manifest.",
        schema_version=1,
        media_types=("application/json", "application/octet-stream"),
        json_schema=_manifest_schema(
            "sfmapi.reconstruction.sparse.v1",
            "sparse_model",
            required=("files",),
        ),
    ),
    "sfmapi.reconstruction.snapshot.v1": ArtifactFormatDefinition(
        format_id="sfmapi.reconstruction.snapshot.v1",
        datatype="sparse_model",
        title="sfmapi sealed snapshot",
        description="Immutable snapshot directory containing portable sparse reconstruction files.",
        schema_version=1,
        media_types=("application/json", "application/octet-stream"),
        json_schema=_manifest_schema(
            "sfmapi.reconstruction.snapshot.v1",
            "sparse_model",
            required=("files",),
        ),
    ),
    "sfmapi.reconstruction.submodel.v1": ArtifactFormatDefinition(
        format_id="sfmapi.reconstruction.submodel.v1",
        datatype="sparse_model",
        title="sfmapi reconstruction submodel",
        description="One disconnected component inside a sparse reconstruction snapshot.",
        schema_version=1,
        media_types=("application/json", "application/octet-stream"),
        json_schema=_manifest_schema(
            "sfmapi.reconstruction.submodel.v1",
            "sparse_model",
            required=("files",),
        ),
    ),
    "sfmapi.projection.images.v1": ArtifactFormatDefinition(
        format_id="sfmapi.projection.images.v1",
        datatype="projection",
        title="sfmapi projected image set",
        description="Projected image files plus a manifest with source/output geometry metadata.",
        schema_version=1,
        media_types=("application/json", "image/jpeg", "image/png"),
        json_schema=_manifest_schema(
            "sfmapi.projection.images.v1",
            "projection",
            required=("source_images", "output_images"),
        ),
    ),
}


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


CORE_ARTIFACT_KINDS: dict[str, ArtifactKindDefinition] = {
    "features.local.v1": ArtifactKindDefinition(
        kind="features.local.v1",
        datatype="feature_set",
        title="Local feature set",
        description=(
            "Portable per-image local keypoints and descriptors. Supports SIFT-like "
            "and learned local features through manifest-declared layouts."
        ),
        durable=False,
        artifact_format="sfmapi.features.local.v1",
        schema_version=1,
    ),
    "features.global.v1": ArtifactKindDefinition(
        kind="features.global.v1",
        datatype="feature_set",
        title="Global image descriptors",
        description="Portable per-image retrieval descriptors such as VLAD or NetVLAD.",
        durable=False,
        artifact_format="sfmapi.features.global.v1",
        schema_version=1,
    ),
    "pairs.image_names.v1": ArtifactKindDefinition(
        kind="pairs.image_names.v1",
        datatype="pair_set",
        title="Image-name pairs",
        description="Portable newline-delimited or manifest-addressed image pair list.",
        durable=False,
        artifact_format="sfmapi.pairs.image_names.v1",
        schema_version=1,
    ),
    "matches.indexed.v1": ArtifactKindDefinition(
        kind="matches.indexed.v1",
        datatype="match_graph",
        title="Indexed feature matches",
        description="Portable raw matches expressed as keypoint-index pairs.",
        durable=False,
        artifact_format="sfmapi.matches.indexed.v1",
        schema_version=1,
    ),
    "matches.coordinates.v1": ArtifactKindDefinition(
        kind="matches.coordinates.v1",
        datatype="match_graph",
        title="Coordinate matches",
        description="Portable detector-free matches expressed as image coordinate pairs.",
        durable=False,
        artifact_format="sfmapi.matches.coordinates.v1",
        schema_version=1,
    ),
    "matches.dense.v1": ArtifactKindDefinition(
        kind="matches.dense.v1",
        datatype="match_graph",
        title="Dense or semi-dense matches",
        description="Portable tiled/chunked dense match field.",
        durable=False,
        artifact_format="sfmapi.matches.dense.v1",
        schema_version=1,
    ),
    "matches.verified.v1": ArtifactKindDefinition(
        kind="matches.verified.v1",
        datatype="match_graph",
        title="Verified two-view geometry",
        description="Portable verified matches with F/E/H matrices and inlier pairs.",
        durable=False,
        artifact_format="sfmapi.matches.verified.v1",
        schema_version=1,
    ),
    "reconstruction.sparse.v1": ArtifactKindDefinition(
        kind="reconstruction.sparse.v1",
        datatype="sparse_model",
        title="Sparse reconstruction",
        description="Portable cameras, image poses, tracks, and sparse points manifest.",
        durable=True,
        artifact_format="sfmapi.reconstruction.sparse.v1",
        schema_version=1,
    ),
    "reconstruction.snapshot": ArtifactKindDefinition(
        kind="reconstruction.snapshot",
        datatype="sparse_model",
        title="Sealed reconstruction snapshot",
        description="Immutable sealed snapshot directory for a reconstruction.",
        durable=True,
        artifact_format="sfmapi.reconstruction.snapshot.v1",
        schema_version=1,
    ),
    "reconstruction.submodel": ArtifactKindDefinition(
        kind="reconstruction.submodel",
        datatype="sparse_model",
        title="Reconstruction submodel",
        description="One disconnected mapping component inside a reconstruction snapshot.",
        durable=True,
        artifact_format="sfmapi.reconstruction.submodel.v1",
        schema_version=1,
    ),
    "projection.images.v1": ArtifactKindDefinition(
        kind="projection.images.v1",
        datatype="projection",
        title="Projected image set",
        description="Images produced by a portable projection transform plus a manifest.",
        durable=True,
        artifact_format="sfmapi.projection.images.v1",
        schema_version=1,
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
    kind_def = CORE_ARTIFACT_KINDS.get(kind)
    if kind_def is None:
        return True
    format_def = CORE_ARTIFACT_FORMATS.get(format_id)
    if format_def is None:
        return True
    return kind_def.datatype == format_def.datatype


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
