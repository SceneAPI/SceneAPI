"""Camera-model registry schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from sfmapi.server.schemas.api.common import Page

ProjectionFamily = Literal["pinhole", "fisheye", "spherical", "omnidirectional", "other"]
DistortionFamily = Literal["none", "radial", "opencv_brown", "opencv_fisheye", "other"]


class CameraModelOut(BaseModel):
    """Portable description of a camera model's parameter layout."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    model: str
    projection: ProjectionFamily
    distortion: DistortionFamily
    params: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    spherical: bool = False
    notes: str | None = None


CameraModelListPage = Page[CameraModelOut]
