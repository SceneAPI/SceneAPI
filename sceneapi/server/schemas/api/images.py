"""Image request/response schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from sceneapi.server.schemas.api.common import Link, Page, TimestampedModel
from sceneapi.server.schemas.api.scene import PosePrior


class ImageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=512)
    blob_sha: str | None = Field(
        None,
        min_length=64,
        max_length=64,
        pattern="^[0-9a-f]{64}$",
    )
    rel_path: str | None = None
    width: int | None = None
    height: int | None = None
    exif: dict | None = None


class BatchCreateImagesRequest(BaseModel):
    """AIP-231 batch-create request body. Each entry is a complete
    ``ImageCreate``; the server registers them all in a single
    transaction."""

    model_config = ConfigDict(extra="forbid")

    requests: list[ImageCreate] = Field(default_factory=list, max_length=1000)


class ImageOut(TimestampedModel):
    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

    image_id: str
    dataset_id: str
    name: str
    content_sha: str
    source_kind: str
    rel_path: str | None = None
    byte_size: int | None = None
    width: int | None = None
    height: int | None = None
    links: dict[str, Link | None] | None = Field(default=None, alias="_links")


class BatchCreateImagesResponse(BaseModel):
    """AIP-231 batch-create response — the same shape every batch
    endpoint should return: a list of the created resources, in
    request-order."""

    images: list[ImageOut] = Field(default_factory=list)


ImageListPage = Page[ImageOut]


class ImageExifResponse(BaseModel):
    """EXIF metadata as a free-form dict. Empty when the source has
    no EXIF or when the bytes can't be located on the worker."""

    exif: dict[str, Any] = Field(default_factory=dict)


class PosePriorsBulkResponse(BaseModel):
    """Per-image PosePrior map for ``GET /v1/datasets/{id}/pose_priors``."""

    pose_priors: dict[str, PosePrior | None] = Field(default_factory=dict)


class PosePriorsBulkWriteResponse(BaseModel):
    """Outcome envelope for ``PUT /v1/datasets/{id}/pose_priors``."""

    written: int
