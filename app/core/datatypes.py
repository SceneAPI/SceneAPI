"""DataType registry — the logical-object axis of the typed dataflow.

The framework's contracts factor along three orthogonal axes:

* **DataType** (here) -- the *logical* object that flows between actions
  (``image_sequence``, ``feature_set``, ``sparse_reconstruction``). This is
  the unit of composition: an action consumes/produces DataType *ids*, and a
  pipeline type-checks by matching them. Type-compatibility is **nominal** --
  ids match or they don't; there is no structural "close enough".
* **Format** (:mod:`app.core.artifacts`) -- a serialization that *realizes*
  one or more DataTypes (``sfmapi.features.local.v1`` realizes ``feature_set``;
  the COLMAP DB is a container format realizing several types at once). The
  portability / media-type / json-schema axis.
* **Artifact** -- a concrete persisted instance in some Format.

Separating the logical type from its serialization is what lets a type have
many formats, keeps composition format-independent, and makes a cross-*format*
coercion (``feature_set[h5] -> feature_set[colmap_db]``) a type-preserving
execution detail that the chain type-check never sees -- distinct from a
cross-*type* action (``feature_set -> match_graph``), which is a real process.

Ownership: this is a repo-owned core contract, a data standard (no plugin
import, no engine link). Plugins declare their action signatures using these
core type-ids; ``tools/gen_contracts.py`` serializes :func:`contract_dict` to
JSON + a C++ ``.inc`` and the ``contract-parity`` gate pins the embed.
"""

from __future__ import annotations

from dataclasses import dataclass

# --- the logical objects ---------------------------------------------------

# A DataType's primary role. ``scene_input`` is typically provided to the
# pipeline (images, cameras); ``artifact`` is typically produced + persisted
# between actions. The role is a hint, not an exclusivity constraint.
DATA_TYPE_KINDS = frozenset({"scene_input", "artifact"})


@dataclass(frozen=True)
class DataType:
    type_id: str           # stable nominal id, the unit of type-compatibility
    title: str
    kind: str              # one of DATA_TYPE_KINDS
    description: str


# Declaration order is the serialization order, so the contract JSON is stable.
CORE_DATA_TYPES: tuple[DataType, ...] = (
    # scene inputs
    DataType("image_sequence", "Image sequence", "scene_input",
             "An ordered collection of images from a single capture."),
    DataType("camera", "Camera", "scene_input",
             "A single camera model (intrinsics + distortion)."),
    DataType("camera_collection", "Camera collection", "scene_input",
             "A collection of camera models, e.g. a multi-camera rig."),
    # artifacts (promoted from the latent artifacts.py artifact_type set)
    DataType("feature_set", "Feature set", "artifact",
             "Per-image keypoints and descriptors (local and/or global)."),
    DataType("pair_spec", "Pair specification", "artifact",
             "A list of image pairs proposed for matching."),
    DataType("match_graph", "Match graph", "artifact",
             "Feature correspondences across image pairs."),
    DataType("sparse_reconstruction", "Sparse reconstruction", "artifact",
             "A sparse SfM model: camera poses, intrinsics, and a point cloud."),
    DataType("projection", "Projection", "artifact",
             "Rendered/projected views or related projection artifacts."),
)

CORE_DATA_TYPES_BY_ID: dict[str, DataType] = {t.type_id: t for t in CORE_DATA_TYPES}

# Back-compat bridge: the legacy artifacts.py ``artifact_type`` strings map to
# DataType ids. Formats keep declaring ``artifact_type``; this promotes it into
# the DataType axis until callers reference type_ids directly.
ARTIFACT_TYPE_TO_DATATYPE: dict[str, str] = {
    "features": "feature_set",
    "pairs": "pair_spec",
    "matches": "match_graph",
    "reconstruction": "sparse_reconstruction",
    "projection": "projection",
}


def is_data_type(type_id: str) -> bool:
    return type_id in CORE_DATA_TYPES_BY_ID


def datatype_for_artifact_type(artifact_type: str) -> str:
    """Map a legacy ``artifact_type`` to its DataType id."""
    try:
        return ARTIFACT_TYPE_TO_DATATYPE[artifact_type]
    except KeyError as exc:
        raise KeyError(
            f"unknown artifact_type {artifact_type!r}; not mapped to a DataType"
        ) from exc


# --- declared contract -----------------------------------------------------

CONTRACT_NAME = "datatypes"
CONTRACT_SCHEMA_VERSION = 1


def contract_dict() -> dict:
    """The DataType registry as a deterministic, JSON-serializable dict."""
    return {
        "contract": CONTRACT_NAME,
        "contract_schema_version": CONTRACT_SCHEMA_VERSION,
        "kinds": sorted(DATA_TYPE_KINDS),
        "types": [
            {
                "type_id": t.type_id,
                "title": t.title,
                "kind": t.kind,
                "description": t.description,
            }
            for t in CORE_DATA_TYPES
        ],
        "artifact_type_aliases": dict(sorted(ARTIFACT_TYPE_TO_DATATYPE.items())),
    }


__all__ = [
    "ARTIFACT_TYPE_TO_DATATYPE",
    "CORE_DATA_TYPES",
    "CORE_DATA_TYPES_BY_ID",
    "CONTRACT_NAME",
    "CONTRACT_SCHEMA_VERSION",
    "DATA_TYPE_KINDS",
    "DataType",
    "contract_dict",
    "datatype_for_artifact_type",
    "is_data_type",
]
