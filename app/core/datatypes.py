"""DataType registry — the data objects of the typed pipeline.

The framework is a typed pipeline of operations over data. DataTypes are the
nouns: the logical objects that flow between operations. They are the only
typed axis -- composition is checked by matching DataType ids nominally
(ids match or they don't; bridging requires an explicit conversion).

DataType is the *logical* object, independent of serialization. One type has
many *formats* (a ``feature_set`` may be COLMAP descriptors or an h5 file);
the Format axis lives in :mod:`app.core.artifacts`. Keeping them separate
means composition is format-independent and a cross-format coercion is a
type-preserving execution detail, not a pipeline edge.

Repo-owned core contract / data standard (no plugin import). Operations
(:mod:`app.core.operations`) declare their ``consumes``/``produces`` over
these ids; pipelines (:mod:`app.core.pipelines`) type-check by threading them.
"""

from __future__ import annotations

from dataclasses import dataclass

DATA_TYPE_KINDS = frozenset({"scene_input", "artifact"})


@dataclass(frozen=True)
class DataType:
    type_id: str  # nominal id -- the unit of type-compatibility
    title: str
    kind: str  # one of DATA_TYPE_KINDS
    description: str


# Declaration order = serialization order (stable contract JSON).
CORE_DATA_TYPES: tuple[DataType, ...] = (
    # scene inputs (provided to a pipeline)
    DataType(
        "image_sequence",
        "Image sequence",
        "scene_input",
        "An ordered collection of images from a single capture.",
    ),
    DataType("camera", "Camera", "scene_input", "A single camera model (intrinsics + distortion)."),
    DataType(
        "camera_collection",
        "Camera collection",
        "scene_input",
        "A collection of camera models, e.g. a multi-camera rig.",
    ),
    # pipeline data (produced by operations)
    DataType("feature_set", "Feature set", "artifact", "Per-image keypoints and descriptors."),
    DataType("pair_set", "Pair set", "artifact", "The image pairs selected for matching."),
    DataType(
        "match_graph",
        "Match graph",
        "artifact",
        "Feature correspondences across image pairs (optionally verified).",
    ),
    DataType(
        "sparse_model",
        "Sparse model",
        "artifact",
        "A sparse SfM model: camera poses, intrinsics, and a point cloud.",
    ),
    DataType(
        "projection",
        "Projection",
        "artifact",
        "Rendered or reprojected views derived from a reconstruction.",
    ),
    # dense_model / splat are deferred until an engine produces them -- the
    # I/O-completeness gate requires a format for every artifact DataType, so
    # they are re-added together with their formats when a producer exists.
)

CORE_DATA_TYPES_BY_ID: dict[str, DataType] = {t.type_id: t for t in CORE_DATA_TYPES}


def is_data_type(type_id: str) -> bool:
    return type_id in CORE_DATA_TYPES_BY_ID


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
                "aliases": [],
                "description": t.description,
            }
            for t in CORE_DATA_TYPES
        ],
    }


__all__ = [
    "CONTRACT_NAME",
    "CONTRACT_SCHEMA_VERSION",
    "CORE_DATA_TYPES",
    "CORE_DATA_TYPES_BY_ID",
    "DATA_TYPE_KINDS",
    "DataType",
    "contract_dict",
    "is_data_type",
]
