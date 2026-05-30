"""Operation registry — the typed verbs of the pipeline.

An Operation is a typed, engine-independent transform: it ``consumes`` and
``produces`` DataTypes (:mod:`app.core.datatypes`). Operations are the
*portable* pipeline stages -- ``features``, ``pairs``, ``matches`` -- not
engine commands. The specific algorithm (SIFT vs SuperPoint, exhaustive vs
retrieval, incremental vs global mapping) is a *parameter* of the operation,
not a new operation: the type system is about data, not algorithms.

This is the composition unit. A pipeline is a sequence of operations whose
types thread together (:mod:`app.core.pipelines`); at execution each operation
is bound to a provider (engine) that implements it. Engine-specific raw
commands (``colmap.feature_extractor``) are a separate escape hatch, outside
this typed model.

Repo-owned core contract / data standard (no plugin import). ``op_id`` is the
portable operation name; providers advertise which operations they implement.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.datatypes import is_data_type


@dataclass(frozen=True)
class Operation:
    op_id: str                  # portable operation name (the pipeline stage)
    title: str
    consumes: tuple[str, ...]   # DataType ids on the input edges
    produces: tuple[str, ...]   # DataType ids on the output edges
    description: str
    # The capability family/families that implement this operation -- the
    # link from the portable capability vocabulary (app.core.capabilities) to
    # the typed operation. The algorithm within a family (sift vs superpoint,
    # incremental vs global) is a parameter, not a separate operation. The
    # operation<->capability consistency gate keeps the two in lockstep.
    capabilities: tuple[str, ...] = ()


# The core SfM / 3DGS pipeline operations. Declaration order = serialization
# order. Multi-input operations (matches, map) are why a pipeline is a typed
# DAG, not a strict chain: an operation may need data from several upstream
# stages, not just its immediate predecessor.
CORE_OPERATIONS: tuple[Operation, ...] = (
    Operation("features", "Feature extraction",
              ("image_sequence",), ("feature_set",),
              "Detect keypoints + compute descriptors per image.",
              capabilities=("features.extract",)),
    Operation("pairs", "Pair selection",
              ("feature_set",), ("pair_set",),
              "Choose which image pairs to match (exhaustive, retrieval, ...).",
              capabilities=("pairs",)),
    Operation("matches", "Feature matching",
              ("feature_set", "pair_set"), ("match_graph",),
              "Match features across the selected pairs.",
              capabilities=("matchers",)),
    Operation("verify", "Geometric verification",
              ("match_graph",), ("match_graph",),
              "Filter matches by two-view geometry.",
              capabilities=("matches.verify", "geometry.two_view")),
    Operation("map", "Mapping (SfM)",
              ("feature_set", "match_graph"), ("sparse_model",),
              "Reconstruct camera poses + sparse points (incremental, global, ...).",
              capabilities=("map",)),
    # --- sparse-model post-processing (sparse_model -> sparse_model) ---
    Operation("triangulate", "Triangulation",
              ("sparse_model", "match_graph"), ("sparse_model",),
              "Triangulate additional 3D points into an existing model.",
              capabilities=("triangulate",)),
    Operation("refine", "Bundle adjustment",
              ("sparse_model",), ("sparse_model",),
              "Jointly refine camera poses, intrinsics, and 3D points.",
              capabilities=("ba",)),
    Operation("optimize_poses", "Pose-graph optimization",
              ("sparse_model",), ("sparse_model",),
              "Optimize the pose graph of a reconstruction.",
              capabilities=("pgo",)),
    Operation("relocalize", "Relocalization",
              ("sparse_model",), ("sparse_model",),
              "Register additional images into an existing reconstruction.",
              capabilities=("relocalize",)),
    Operation("merge", "Reconstruction merge",
              ("sparse_model",), ("sparse_model",),
              "Merge disconnected submodels into one reconstruction.",
              capabilities=("recon.merge",)),
    Operation("georegister", "Georegistration",
              ("sparse_model",), ("sparse_model",),
              "Align a reconstruction to a geographic / metric frame.",
              capabilities=("georegister",)),
    Operation("undistort", "Undistortion",
              ("sparse_model",), ("sparse_model",),
              "Undistort images and emit adjusted intrinsics.",
              capabilities=("image.undistort",)),
    # --- image reprojection ---
    Operation("project", "Reprojection",
              ("image_sequence",), ("projection",),
              "Reproject images between equirectangular / cubemap / perspective.",
              capabilities=("projection", "spherical")),
    # dense / splat operations are deferred with their output DataTypes
    # (dense_model / splat) until an engine produces them.
)

OPERATIONS_BY_ID: dict[str, Operation] = {op.op_id: op for op in CORE_OPERATIONS}

# Fail fast at import if an edge references an unknown DataType.
for _op in CORE_OPERATIONS:
    for _t in (*_op.consumes, *_op.produces):
        if not is_data_type(_t):
            raise ValueError(
                f"operation {_op.op_id!r} references unknown DataType {_t!r}"
            )


def operation_for(op_id: str) -> Operation | None:
    return OPERATIONS_BY_ID.get(op_id)


CONTRACT_NAME = "operations"
CONTRACT_SCHEMA_VERSION = 1


def contract_dict() -> dict:
    """The operation registry as a deterministic, JSON-serializable dict."""
    return {
        "contract": CONTRACT_NAME,
        "contract_schema_version": CONTRACT_SCHEMA_VERSION,
        "operations": [
            {
                "op_id": op.op_id,
                "title": op.title,
                "consumes": list(op.consumes),
                "produces": list(op.produces),
                "capabilities": list(op.capabilities),
                "description": op.description,
            }
            for op in CORE_OPERATIONS
        ],
    }


__all__ = [
    "CORE_OPERATIONS",
    "CONTRACT_NAME",
    "CONTRACT_SCHEMA_VERSION",
    "OPERATIONS_BY_ID",
    "Operation",
    "contract_dict",
    "operation_for",
]
