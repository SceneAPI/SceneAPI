"""Dataset/source request/response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.api.common import Link, TimestampedModel


class UploadEntrySpec(BaseModel):
    """One image entry in an :class:`UploadSourceSpec`. Each entry binds
    a human-readable filename to a previously-finalized upload's
    canonical content-addressed sha (returned by ``POST
    /v1/uploads/{id}:finalize``)."""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(..., min_length=1, max_length=512)
    blob_sha: str = Field(..., min_length=64, max_length=64)


class UploadSourceSpec(BaseModel):
    """Image source backed by previously-uploaded blobs (sfmapi owns
    the bytes via the content-addressed blob store)."""

    model_config = ConfigDict(extra="ignore")

    kind: Literal["upload"] = "upload"
    entries: list[UploadEntrySpec] = Field(default_factory=list)


class LocalSourceSpec(BaseModel):
    """Image source pointing at a worker-local directory tree. Bytes
    are NEVER copied — workers stream from ``root`` in place. Locked
    by ``L3`` in ``docs/guides/decisions.md`` (50GB local dirs)."""

    model_config = ConfigDict(extra="ignore")

    kind: Literal["local"] = "local"
    root: str
    recursive: bool = True


class S3SourceSpec(BaseModel):
    """Image source backed by an S3 prefix. Bytes are lazy-downloaded
    to the worker's LRU cache on first read; remote-only by default."""

    model_config = ConfigDict(extra="ignore")

    kind: Literal["s3"] = "s3"
    bucket: str
    prefix: str


SourceSpec = Annotated[
    UploadSourceSpec | LocalSourceSpec | S3SourceSpec,
    Field(discriminator="kind"),
]
"""Tagged union for dataset image inputs, discriminated on ``kind``:

- ``upload`` -> :class:`UploadSourceSpec`
- ``local``  -> :class:`LocalSourceSpec`
- ``s3``     -> :class:`S3SourceSpec`

Forward-compatibility: SDKs should reject unknown ``kind`` values
client-side rather than guessing. Source-kind availability is validated
by dataset creation; backend-native source importers live under
``/v1/backend/actions`` rather than portable capability flags."""


class DatasetCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(..., min_length=1, max_length=255)
    source: SourceSpec
    camera_model: str = "SIMPLE_RADIAL"
    intrinsics_mode: Literal["single_camera", "per_image", "per_folder"] = "single_camera"
    is_spherical: bool = False
    rig_config: dict | None = None
    respect_exif_orientation: bool = False


class DatasetPatch(BaseModel):
    """Partial update. Unset fields are left untouched. The dataset's
    `source_id` is immutable — to change images, create a new dataset."""

    model_config = ConfigDict(extra="ignore")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    camera_model: str | None = None
    intrinsics_mode: Literal["single_camera", "per_image", "per_folder"] | None = None
    is_spherical: bool | None = None
    rig_config: dict | None = None
    respect_exif_orientation: bool | None = None
    active_maskset_id: str | None = None


class DatasetOut(TimestampedModel):
    """Wire shape of a Dataset row.

    A Dataset binds an image source (``source_id`` -> one of the
    :data:`SourceSpec` variants) to per-dataset SfM settings:
    ``camera_model`` (COLMAP camera-model string), ``intrinsics_mode``
    (single shared / per-image / per-folder), and the spherical /
    rig metadata. ``manifest_hash`` is the content-addressed
    fingerprint of the materialized image set; downstream stages
    (features, similarity) cache against it.
    """

    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

    dataset_id: str
    tenant_id: str
    project_id: str
    source_id: str
    name: str
    camera_model: str
    intrinsics_mode: str
    is_spherical: bool
    respect_exif_orientation: bool
    rig_config_json: dict | None = Field(default=None, alias="rig_config_json")
    active_maskset_id: str | None = None
    manifest_hash: str
    updated_at: datetime | None = None
    links: dict[str, Link | None] | None = Field(default=None, alias="_links")
