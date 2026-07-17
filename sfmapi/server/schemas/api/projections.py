"""Projection utility request and manifest schemas."""

from __future__ import annotations

from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sfmapi.server.core.projections import CUBEMAP_FACE_AXES, CUBEMAP_FACE_ORDER
from sfmapi.server.schemas.pipeline_spec import (
    PROVIDER_SELECTOR_MAX_LENGTH,
    PROVIDER_SELECTOR_PATTERN,
)

CubemapFace = Literal["front", "right", "back", "left", "up", "down"]
ProjectionOperation = Literal[
    "equirectangular_to_cubemap",
    "cubemap_to_equirectangular",
    "equirectangular_to_perspective",
]
ProjectionInterpolation = Literal["nearest", "linear", "cubic", "lanczos"]
ProjectionOutputFormat = Literal["source", "jpg", "png", "webp"]


class ProjectionSampling(BaseModel):
    """Portable sampling controls for image projection jobs."""

    model_config = ConfigDict(extra="forbid")

    interpolation: ProjectionInterpolation = "linear"
    antialias: bool = True
    seam_padding_px: int = Field(default=0, ge=0, le=256)
    overlap_px: int = Field(default=0, ge=0, le=1024)


class ProjectionOutputOptions(BaseModel):
    """Portable output controls shared by projection jobs."""

    model_config = ConfigDict(extra="forbid")

    format: ProjectionOutputFormat = "source"
    jpeg_quality: int = Field(default=92, ge=1, le=100)
    write_manifest: bool = True
    create_dataset: bool = True
    dataset_name: str | None = Field(default=None, min_length=1, max_length=255)


class CubemapProjectionSpec(BaseModel):
    """Equirectangular panorama to six cubemap faces."""

    model_config = ConfigDict(extra="forbid")

    convention: Literal["sfmapi-opencv"] = "sfmapi-opencv"
    face_size: int = Field(default=1024, ge=64, le=8192)
    face_order: list[CubemapFace] = Field(
        default_factory=lambda: cast(list[CubemapFace], list(CUBEMAP_FACE_ORDER))
    )
    sampling: ProjectionSampling = Field(default_factory=ProjectionSampling)
    output: ProjectionOutputOptions = Field(default_factory=ProjectionOutputOptions)

    @model_validator(mode="after")
    def _validate_face_order(self) -> CubemapProjectionSpec:
        if len(self.face_order) != len(CUBEMAP_FACE_ORDER) or set(self.face_order) != set(
            CUBEMAP_FACE_ORDER
        ):
            expected = ", ".join(CUBEMAP_FACE_ORDER)
            raise ValueError(f"face_order must contain each cubemap face exactly once: {expected}")
        return self


class EquirectangularProjectionSpec(BaseModel):
    """Cubemap faces back to an equirectangular panorama."""

    model_config = ConfigDict(extra="forbid")

    convention: Literal["sfmapi-opencv"] = "sfmapi-opencv"
    width: int | None = Field(default=None, ge=64, le=65536)
    height: int | None = Field(default=None, ge=32, le=32768)
    sampling: ProjectionSampling = Field(default_factory=ProjectionSampling)
    output: ProjectionOutputOptions = Field(default_factory=ProjectionOutputOptions)

    @model_validator(mode="after")
    def _validate_dimensions(self) -> EquirectangularProjectionSpec:
        if (self.width is None) != (self.height is None):
            raise ValueError("width and height must be provided together")
        if self.width is not None and self.height is not None and self.width != self.height * 2:
            raise ValueError("equirectangular width must equal 2 * height")
        return self


class PerspectiveViewSpec(BaseModel):
    """One pinhole view sampled from an equirectangular panorama."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=128)
    yaw_deg: float = Field(default=0.0, ge=-360.0, le=360.0)
    pitch_deg: float = Field(default=0.0, ge=-90.0, le=90.0)
    roll_deg: float = Field(default=0.0, ge=-180.0, le=180.0)
    hfov_deg: float = Field(default=90.0, gt=0.0, le=179.0)
    width: int = Field(default=1024, ge=16, le=16384)
    height: int = Field(default=1024, ge=16, le=16384)


class PerspectiveProjectionSpec(BaseModel):
    """Equirectangular panorama to one or more perspective images."""

    model_config = ConfigDict(extra="forbid")

    convention: Literal["sfmapi-opencv"] = "sfmapi-opencv"
    views: list[PerspectiveViewSpec] = Field(
        default_factory=lambda: [PerspectiveViewSpec(name="view_000")],
        min_length=1,
        max_length=1024,
    )
    sampling: ProjectionSampling = Field(default_factory=ProjectionSampling)
    output: ProjectionOutputOptions = Field(default_factory=ProjectionOutputOptions)


class ProjectionJobRequest(BaseModel):
    """Dataset-level projection job request.

    Only the spec matching ``operation`` is used. Missing matching specs
    are filled with their portable defaults.
    """

    model_config = ConfigDict(extra="forbid")

    operation: ProjectionOperation = "equirectangular_to_cubemap"
    cubemap: CubemapProjectionSpec | None = None
    equirectangular: EquirectangularProjectionSpec | None = None
    perspective: PerspectiveProjectionSpec | None = None
    provider: str | None = Field(
        default=None,
        min_length=1,
        max_length=PROVIDER_SELECTOR_MAX_LENGTH,
        pattern=PROVIDER_SELECTOR_PATTERN,
        description="Optional provider id to execute this projection job.",
    )

    @model_validator(mode="after")
    def _fill_default_spec(self) -> ProjectionJobRequest:
        if self.operation == "equirectangular_to_cubemap" and self.cubemap is None:
            self.cubemap = CubemapProjectionSpec()
        elif self.operation == "cubemap_to_equirectangular" and self.equirectangular is None:
            self.equirectangular = EquirectangularProjectionSpec()
        elif self.operation == "equirectangular_to_perspective" and self.perspective is None:
            self.perspective = PerspectiveProjectionSpec()
        return self

    def operation_spec(self) -> dict[str, object]:
        """Return the concrete spec for ``operation`` as JSON-ready data.

        The top-level ``provider`` selector is stamped onto the operation
        spec so workers reading ``backend_for_stage(operation_spec)`` route
        correctly without needing the surrounding envelope.
        """
        if self.operation == "equirectangular_to_cubemap":
            base = self.cubemap.model_dump(mode="json") if self.cubemap else {}
        elif self.operation == "cubemap_to_equirectangular":
            base = self.equirectangular.model_dump(mode="json") if self.equirectangular else {}
        elif self.operation == "equirectangular_to_perspective":
            base = self.perspective.model_dump(mode="json") if self.perspective else {}
        else:
            raise ValueError(f"unsupported projection operation: {self.operation}")
        if self.provider is not None:
            base["provider"] = self.provider
        return base


class CubemapProjectionRequest(ProjectionJobRequest):
    operation: Literal["equirectangular_to_cubemap"] = "equirectangular_to_cubemap"


class EquirectangularProjectionRequest(ProjectionJobRequest):
    operation: Literal["cubemap_to_equirectangular"] = "cubemap_to_equirectangular"


class PerspectiveProjectionRequest(ProjectionJobRequest):
    operation: Literal["equirectangular_to_perspective"] = "equirectangular_to_perspective"


class ProjectionManifest(BaseModel):
    """Worker-emitted manifest for projected image sets."""

    schema_version: int = 1
    operation: ProjectionOperation
    convention: Literal["sfmapi-opencv"] = "sfmapi-opencv"
    face_order: list[CubemapFace] | None = None
    face_axes: dict[str, dict[str, list[int]]] | None = None
    spec: dict[str, object]
    output_path: str
    files: list[str] = Field(default_factory=list)
    source_images: list[dict[str, object]] = Field(default_factory=list)
    output_images: list[dict[str, object]] = Field(default_factory=list)
    derived_dataset: dict[str, object] | None = None
    backend_result: dict[str, object] = Field(default_factory=dict)


def manifest_geometry_for_operation(operation: str) -> dict[str, object]:
    if operation != "equirectangular_to_cubemap":
        return {}
    return {
        "face_order": list(CUBEMAP_FACE_ORDER),
        "face_axes": CUBEMAP_FACE_AXES,
    }


__all__ = [
    "CubemapProjectionRequest",
    "CubemapProjectionSpec",
    "EquirectangularProjectionRequest",
    "EquirectangularProjectionSpec",
    "PerspectiveProjectionRequest",
    "PerspectiveProjectionSpec",
    "PerspectiveViewSpec",
    "ProjectionJobRequest",
    "ProjectionManifest",
    "ProjectionOutputOptions",
    "ProjectionSampling",
    "manifest_geometry_for_operation",
]
