"""Processor registry - typed verbs with named input/output ports.

A Processor consumes named Data Type ports, supplies named Data Type ports, and
declares portable attributes. This is the named-port form of the older
Operation registry; app.core.operations remains as a compatibility projection.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.core.attributes import Attribute, AttributeSet
from app.core.config_stages import VALID_CONFIG_STAGES
from app.core.datatypes import is_data_type

_ROLE_RE = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class PortSpec:
    datatype: str
    required: bool = True
    multiple: bool = False
    description: str = ""

    def contract_dict(self) -> dict[str, Any]:
        return {
            "datatype": self.datatype,
            "required": self.required,
            "multiple": self.multiple,
            "description": self.description,
        }


@dataclass(frozen=True)
class Processor:
    processor_id: str
    title: str
    consumer: dict[str, PortSpec]
    supplier: dict[str, PortSpec]
    attributes: AttributeSet = ()
    description: str = ""
    capabilities: tuple[str, ...] = ()
    config_stage: str | None = None
    aliases: tuple[str, ...] = ()
    special_inputs: dict[str, PortSpec] = field(default_factory=dict)
    special_attributes: AttributeSet = ()

    @property
    def id(self) -> str:
        return self.processor_id

    @property
    def op_id(self) -> str:
        return self.processor_id

    @property
    def consumes(self) -> tuple[str, ...]:
        return tuple(port.datatype for port in self.consumer.values())

    @property
    def produces(self) -> tuple[str, ...]:
        return tuple(port.datatype for port in self.supplier.values())

    def contract_dict(self) -> dict[str, Any]:
        return {
            "processor_id": self.processor_id,
            "title": self.title,
            "consumer": {
                role: port.contract_dict() for role, port in sorted(self.consumer.items())
            },
            "supplier": {
                role: port.contract_dict() for role, port in sorted(self.supplier.items())
            },
            "attributes": [attr.contract_dict() for attr in self.attributes],
            "special_inputs": {
                role: port.contract_dict() for role, port in sorted(self.special_inputs.items())
            },
            "special_attributes": [attr.contract_dict() for attr in self.special_attributes],
            "capabilities": list(self.capabilities),
            "config_stage": self.config_stage,
            "aliases": list(self.aliases),
            "description": self.description,
        }

    def operation_contract_dict(self) -> dict[str, Any]:
        """Legacy flat Operation projection used by /v1/operations."""
        return {
            "op_id": self.op_id,
            "title": self.title,
            "consumes": list(self.consumes),
            "produces": list(self.produces),
            "capabilities": list(self.capabilities),
            "config_stage": self.config_stage,
            "description": self.description,
        }


def _port(
    datatype: str, description: str, *, required: bool = True, multiple: bool = False
) -> PortSpec:
    return PortSpec(
        datatype=datatype,
        required=required,
        multiple=multiple,
        description=description,
    )


def _attr(
    name: str,
    type_: str,
    description: str,
    *,
    default: Any = None,
    has_default: bool = False,
    enum: tuple[str, ...] = (),
    min: int | float | None = None,
    max: int | float | None = None,
    required: bool = False,
) -> Attribute:
    return Attribute(
        name=name,
        type=type_,
        required=required,
        default=default,
        has_default=has_default,
        enum=enum,
        min=min,
        max=max,
        description=description,
    )


FEATURE_ATTRIBUTES: AttributeSet = (
    _attr(
        "type",
        "enum",
        "Feature extractor family.",
        default="sift",
        has_default=True,
        enum=("sift", "superpoint", "aliked", "disk", "r2d2", "d2net", "sosnet"),
    ),
    _attr(
        "max_num_features",
        "int",
        "Maximum features per image.",
        default=8192,
        has_default=True,
        min=0,
    ),
    _attr(
        "use_gpu", "bool", "Whether GPU acceleration may be used.", default=True, has_default=True
    ),
    _attr("seed", "int", "Deterministic seed.", default=0, has_default=True),
)

PAIR_ATTRIBUTES: AttributeSet = (
    _attr(
        "strategy",
        "enum",
        "Image pair selection strategy.",
        default="exhaustive",
        has_default=True,
        enum=(
            "exhaustive",
            "sequential",
            "spatial",
            "vocabtree",
            "retrieval",
            "from_poses",
            "explicit",
        ),
    ),
    _attr("overlap", "int", "Sequential overlap window.", default=10, has_default=True, min=0),
    _attr(
        "retrieval_k", "int", "Number of retrieval neighbors.", default=20, has_default=True, min=1
    ),
)

MATCH_ATTRIBUTES: AttributeSet = (
    _attr(
        "type",
        "enum",
        "Feature matcher family.",
        default="nn-mutual",
        has_default=True,
        enum=("nn-mutual", "nn-ratio", "superglue", "lightglue", "loftr", "mast3r"),
    ),
    _attr(
        "use_gpu", "bool", "Whether GPU acceleration may be used.", default=True, has_default=True
    ),
    _attr(
        "cross_check",
        "bool",
        "Require reciprocal nearest neighbors.",
        default=True,
        has_default=True,
    ),
    _attr(
        "max_ratio",
        "float",
        "Lowe ratio threshold.",
        default=0.8,
        has_default=True,
        min=0.0,
        max=1.0,
    ),
    _attr(
        "max_distance",
        "float",
        "Descriptor distance threshold.",
        default=0.7,
        has_default=True,
        min=0.0,
    ),
)

VERIFY_ATTRIBUTES: AttributeSet = (
    _attr(
        "use_gpu", "bool", "Whether GPU acceleration may be used.", default=True, has_default=True
    ),
    _attr(
        "min_inlier_ratio",
        "float",
        "Minimum accepted inlier ratio.",
        default=0.25,
        has_default=True,
        min=0.0,
        max=1.0,
    ),
)

MAP_ATTRIBUTES: AttributeSet = (
    _attr(
        "kind",
        "enum",
        "Mapping recipe.",
        default="incremental",
        has_default=True,
        enum=("incremental", "global", "hierarchical", "spherical"),
    ),
    _attr("seed", "int", "Deterministic seed.", default=0, has_default=True),
    _attr("max_runtime_seconds", "int", "Optional runtime budget in seconds.", min=1),
)

REFINE_ATTRIBUTES: AttributeSet = (
    _attr(
        "mode",
        "enum",
        "Bundle-adjustment mode.",
        default="standard",
        has_default=True,
        enum=("standard", "two_stage", "featuremetric", "rig"),
    ),
    _attr(
        "max_num_iterations",
        "int",
        "Maximum solver iterations.",
        default=100,
        has_default=True,
        min=1,
    ),
)


CORE_PROCESSORS: tuple[Processor, ...] = (
    Processor(
        "features",
        "Feature extraction",
        {"images": _port("image_sequence", "Captured image sequence.")},
        {"features": _port("feature_set", "Per-image keypoints and descriptors.")},
        FEATURE_ATTRIBUTES,
        "Detect keypoints + compute descriptors per image.",
        capabilities=("features.extract",),
        config_stage="features",
    ),
    Processor(
        "pairs",
        "Pair selection",
        {"features": _port("feature_set", "Feature set used for retrieval or metadata.")},
        {"pairs": _port("pair_set", "Selected image pairs.")},
        PAIR_ATTRIBUTES,
        "Choose which image pairs to match (exhaustive, retrieval, ...).",
        capabilities=("pairs",),
        config_stage="pairs",
    ),
    Processor(
        "matches",
        "Feature matching",
        {
            "features": _port("feature_set", "Features to match."),
            "pairs": _port("pair_set", "Image pairs to evaluate."),
        },
        {"matches": _port("match_graph", "Raw feature correspondences.")},
        MATCH_ATTRIBUTES,
        "Match features across the selected pairs.",
        capabilities=("matchers",),
        config_stage="matcher",
    ),
    Processor(
        "verify",
        "Geometric verification",
        {"matches": _port("match_graph", "Candidate correspondences.")},
        {"matches": _port("match_graph", "Geometrically verified correspondences.")},
        VERIFY_ATTRIBUTES,
        "Filter matches by two-view geometry.",
        capabilities=("matches.verify", "geometry.two_view"),
        config_stage="verify",
    ),
    Processor(
        "map",
        "Mapping (SfM)",
        {
            "features": _port("feature_set", "Feature observations."),
            "matches": _port("match_graph", "Verified correspondences."),
        },
        {"model": _port("sparse_model", "Sparse reconstruction model.")},
        MAP_ATTRIBUTES,
        "Reconstruct camera poses + sparse points (incremental, global, ...).",
        capabilities=("map",),
        config_stage="mapping",
    ),
    Processor(
        "triangulate",
        "Triangulation",
        {
            "model": _port("sparse_model", "Existing sparse model."),
            "matches": _port("match_graph", "Additional correspondences."),
        },
        {"model": _port("sparse_model", "Sparse model with added points.")},
        (),
        "Triangulate additional 3D points into an existing model.",
        capabilities=("triangulate",),
    ),
    Processor(
        "refine",
        "Bundle adjustment",
        {"model": _port("sparse_model", "Sparse model to refine.")},
        {"model": _port("sparse_model", "Refined sparse model.")},
        REFINE_ATTRIBUTES,
        "Jointly refine camera poses, intrinsics, and 3D points.",
        capabilities=("ba",),
        config_stage="bundle_adjustment",
    ),
    Processor(
        "optimize_poses",
        "Pose-graph optimization",
        {"model": _port("sparse_model", "Sparse model to optimize.")},
        {"model": _port("sparse_model", "Pose-optimized sparse model.")},
        (),
        "Optimize the pose graph of a reconstruction.",
        capabilities=("pgo",),
    ),
    Processor(
        "relocalize",
        "Relocalization",
        {"model": _port("sparse_model", "Reference sparse model.")},
        {"model": _port("sparse_model", "Sparse model with registered images.")},
        (),
        "Register additional images into an existing reconstruction.",
        capabilities=("relocalize",),
    ),
    Processor(
        "merge",
        "Reconstruction merge",
        {"model": _port("sparse_model", "Sparse model component.", multiple=True)},
        {"model": _port("sparse_model", "Merged sparse model.")},
        (),
        "Merge disconnected submodels into one reconstruction.",
        capabilities=("recon.merge",),
    ),
    Processor(
        "georegister",
        "Georegistration",
        {"model": _port("sparse_model", "Sparse model to align.")},
        {"model": _port("sparse_model", "Georegistered sparse model.")},
        (),
        "Align a reconstruction to a geographic / metric frame.",
        capabilities=("georegister",),
    ),
    Processor(
        "undistort",
        "Undistortion",
        {"model": _port("sparse_model", "Sparse model with source cameras.")},
        {"model": _port("sparse_model", "Sparse model with adjusted intrinsics.")},
        (),
        "Undistort images and emit adjusted intrinsics.",
        capabilities=("image.undistort",),
    ),
    Processor(
        "project",
        "Reprojection",
        {"images": _port("image_sequence", "Images to reproject.")},
        {"projection": _port("projection", "Projected image set.")},
        (),
        "Reproject images between equirectangular / cubemap / perspective.",
        capabilities=("projection", "spherical"),
    ),
)

_processor_ids = [p.processor_id for p in CORE_PROCESSORS]
if len(_processor_ids) != len(set(_processor_ids)):
    raise ValueError("duplicate core processor id")

_aliases = [alias for p in CORE_PROCESSORS for alias in p.aliases]
if len(_aliases) != len(set(_aliases)):
    raise ValueError("duplicate core processor alias")
if set(_aliases) & set(_processor_ids):
    raise ValueError("processor alias collides with a processor id")

PROCESSORS_BY_ID: dict[str, Processor] = {p.processor_id: p for p in CORE_PROCESSORS}
PROCESSOR_ALIASES: dict[str, str] = {
    alias: p.processor_id for p in CORE_PROCESSORS for alias in p.aliases
}


def _validate_processor(p: Processor) -> None:
    for group_name, ports in (
        ("consumer", p.consumer),
        ("supplier", p.supplier),
        ("special_inputs", p.special_inputs),
    ):
        for role, port in ports.items():
            if not _ROLE_RE.match(role):
                raise ValueError(
                    f"processor {p.processor_id!r} has invalid {group_name} role {role!r}"
                )
            if not is_data_type(port.datatype):
                raise ValueError(
                    f"processor {p.processor_id!r} references unknown DataType {port.datatype!r}"
                )
    if set(p.consumer) & set(p.special_inputs):
        raise ValueError(f"processor {p.processor_id!r} has colliding core/special input roles")
    attr_names = [a.name for a in (*p.attributes, *p.special_attributes)]
    if len(attr_names) != len(set(attr_names)):
        raise ValueError(f"processor {p.processor_id!r} has duplicate attributes")
    if p.config_stage is not None and p.config_stage not in VALID_CONFIG_STAGES:
        raise ValueError(
            f"processor {p.processor_id!r} has unknown config_stage {p.config_stage!r}"
        )


for _processor in CORE_PROCESSORS:
    _validate_processor(_processor)


def processor_for(processor_id: str) -> Processor | None:
    return PROCESSORS_BY_ID.get(PROCESSOR_ALIASES.get(processor_id, processor_id))


_PROCESSOR_BY_CAPABILITY_FAMILY: dict[str, str] = {
    family: p.processor_id for p in CORE_PROCESSORS for family in p.capabilities
}


def processor_for_capability(capability: str) -> str | None:
    """The processor a capability implements, or None for infrastructure."""
    for family in sorted(_PROCESSOR_BY_CAPABILITY_FAMILY, key=len, reverse=True):
        if capability == family or capability.startswith(family + "."):
            return _PROCESSOR_BY_CAPABILITY_FAMILY[family]
    return None


def processors_for_capabilities(capabilities: object) -> set[str]:
    out: set[str] = set()
    for cap in capabilities:  # type: ignore[attr-defined]
        proc = processor_for_capability(str(cap))
        if proc is not None:
            out.add(proc)
    return out


CONTRACT_NAME = "processors"
CONTRACT_SCHEMA_VERSION = 1


def contract_dict() -> dict[str, Any]:
    return {
        "contract": CONTRACT_NAME,
        "contract_schema_version": CONTRACT_SCHEMA_VERSION,
        "processors": [p.contract_dict() for p in CORE_PROCESSORS],
        "rules": {
            "composition": (
                "A.supplier[out] -> B.consumer[in] is legal iff their "
                "PortSpec.datatype values match exactly; current match_graph "
                "verification is a compatibility refinement captured by the "
                "Pipeline contract until raw/verified match graphs split."
            ),
            "port_datatype": "singular",
            "plugin_core_contract": "closed",
            "special_inputs": "optional",
        },
    }


__all__ = [
    "CONTRACT_NAME",
    "CONTRACT_SCHEMA_VERSION",
    "CORE_PROCESSORS",
    "PROCESSORS_BY_ID",
    "PROCESSOR_ALIASES",
    "PortSpec",
    "Processor",
    "contract_dict",
    "processor_for",
    "processor_for_capability",
    "processors_for_capabilities",
]
