"""Request bodies for the portable reconstruction/dataset stage routes.

These cover the decomposed pipeline stages that operate on an existing
reconstruction or dataset rather than on raw images:

  - reconstruction-scoped: ``:bundleAdjust`` (see
    :class:`app.schemas.pipeline_spec.BundleAdjustmentSpec`),
    ``:triangulate``, ``:poseGraphOptimize``, ``:export``,
    ``:relocalize``, ``:undistort``
  - dataset-scoped: ``:buildVocabTree``, ``:configureRig``,
    ``:estimateTwoView``

Every spec carries the optional ``provider`` selector (sfm_hub
provider id) and a ``backend_options`` bag. ``extra="forbid"`` so a
typo'd field is a 422 rather than a silently ignored option.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.api.scene import Sim3

_PROVIDER_COMPONENT_MAX_LENGTH = 64
_PROVIDER_PATTERN = (
    r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}"
    r"(?:@[A-Za-z0-9][A-Za-z0-9_.-]{0,63})?$"
)
_PROVIDER_MAX_LENGTH = _PROVIDER_COMPONENT_MAX_LENGTH * 2 + 1


class _PortableStageSpec(BaseModel):
    """Shared shape for portable stage request bodies."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    provider: str | None = Field(
        default=None,
        min_length=1,
        max_length=_PROVIDER_MAX_LENGTH,
        pattern=_PROVIDER_PATTERN,
        description=(
            "Optional sfm_hub provider id to execute this stage. When unset, "
            "the server resolves one through routing profiles."
        ),
    )
    backend_options: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Backend-specific options. Discover supported keys with GET /v1/backend/config-schemas."
        ),
    )


class TriangulateSpec(_PortableStageSpec):
    """``POST /v1/reconstructions/{rid}:triangulate`` — re-triangulate
    against the reconstruction's existing feature database
    (capability ``triangulate.retri``)."""


class PoseGraphSpec(_PortableStageSpec):
    """``POST /v1/reconstructions/{rid}:poseGraphOptimize`` — pose-graph
    optimization over the reconstruction (capability ``pgo.optimize``)."""

    max_num_iterations: int = Field(default=100, ge=1, le=10000)


ExportFormat = Literal[
    "ply",
    "nvm",
    "colmap_text",
    "colmap_bin",
    "nerfstudio",
    "gaussian_splatting",
    "instant_ngp",
    "kapture",
]
"""Portable export targets. The capability flag is ``export.{format}``."""


class ExportSpec(_PortableStageSpec):
    """``POST /v1/reconstructions/{rid}:export`` — export the sparse model
    to a portable interchange format (capability ``export.{format}``)."""

    format: ExportFormat = "ply"


class RelocalizeSpec(_PortableStageSpec):
    """``POST /v1/reconstructions/{rid}:relocalize`` — register additional
    images into an existing reconstruction (capability
    ``relocalize.images``)."""

    image_ids: list[int] = Field(
        default_factory=list,
        description=(
            "Backend image ids to relocalize. Empty means the backend "
            "relocalizes every not-yet-registered image it can."
        ),
    )


class UndistortSpec(_PortableStageSpec):
    """``POST /v1/reconstructions/{rid}:undistort`` — rewrite images to a
    distortion-free camera model and emit adjusted intrinsics
    (capability ``image.undistort``)."""


class VocabTreeSpec(_PortableStageSpec):
    """``POST /v1/datasets/{did}:buildVocabTree`` — build a reusable
    vocabulary-tree retrieval index from the dataset's feature database
    (capability ``index.vocab_tree``)."""


class RigConfigSpec(_PortableStageSpec):
    """``POST /v1/datasets/{did}:configureRig`` — declare or calibrate a
    multi-camera rig over the dataset's feature database
    (capability ``rigs.configure``)."""

    rig_config: dict[str, Any] = Field(
        default_factory=dict,
        description="Portable rig declaration. Backend-specific calibration controls go in backend_options.",
    )


class TwoViewSpec(_PortableStageSpec):
    """``POST /v1/datasets/{did}:estimateTwoView`` — estimate two-view
    geometry (E/F/H + relative pose) for image pairs in the dataset's
    feature database (capability ``geometry.two_view``)."""


GeoregisterMode = Literal["sim3", "gps"]
"""``sim3`` applies a caller-supplied transform; ``gps`` solves one from
georeferenced inputs (GPS / geo-tags / control points)."""


class GeoregisterRequest(_PortableStageSpec):
    """``POST /v1/reconstructions/{rid}:georegister`` request body.

    ``mode=sim3`` (default) applies the supplied :class:`Sim3` transform
    via the backend's ``apply_sim3`` (capability ``georegister.sim3``).
    ``mode=gps`` solves the transform from georeferenced inputs via
    ``align_reconstruction`` (capability ``georegister.gps``); the
    georeferenced inputs are read from the reconstruction + the
    ``backend_options`` bag.
    """

    mode: GeoregisterMode = "sim3"
    sim3: Sim3 | None = Field(
        default=None,
        description="Required when mode='sim3'; rejected when mode='gps'.",
    )

    @model_validator(mode="after")
    def _check_mode(self) -> GeoregisterRequest:
        if self.mode == "sim3" and self.sim3 is None:
            raise ValueError("mode='sim3' requires a sim3 transform")
        if self.mode == "gps" and self.sim3 is not None:
            raise ValueError("mode='gps' solves the transform; do not pass sim3")
        return self


__all__ = [
    "ExportFormat",
    "ExportSpec",
    "GeoregisterMode",
    "GeoregisterRequest",
    "PoseGraphSpec",
    "RelocalizeSpec",
    "RigConfigSpec",
    "TriangulateSpec",
    "TwoViewSpec",
    "UndistortSpec",
    "VocabTreeSpec",
]
