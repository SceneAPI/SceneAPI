"""Reconstruction + SubModel response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.public_outputs import sanitize_public_outputs
from app.schemas.api.common import Link, LinkedModel
from app.schemas.pipeline_spec import PipelineSpec

ReconstructionStatus = Literal[
    "running",
    "succeeded",
    "failed",
    "cancelled",
    "cancelled_dirty",
]
"""Closed set of reconstruction lifecycle states (AIP-216).

A ``Reconstruction`` row is created at job-submit time in ``running``
and rolls up to a terminal state alongside its driving ``Job``.
"""


class ReconstructionOut(LinkedModel):
    """Wire shape of a Reconstruction row.

    A Reconstruction is a single mapping run against a dataset. It
    produces N :class:`SubModelOut` rows (one per disconnected
    component COLMAP discovers). The reconstruction itself is
    metadata; the actual outputs live as sealed snapshots reachable
    via ``links['snapshots']``. ``rv_id`` is the runtime-version
    fingerprint that gates cache lookup; ``dataset_snapshot_hash``
    pins the input image set for reproducibility.
    """

    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

    recon_id: str
    project_id: str
    dataset_id: str
    dataset_snapshot_hash: str
    spec: PipelineSpec = Field(..., validation_alias="spec_json")
    rv_id: str
    status: ReconstructionStatus
    created_at: datetime
    links: dict[str, Link | None] | None = Field(default=None, alias="_links")

    @model_validator(mode="after")
    def _sanitize_public_spec(self) -> ReconstructionOut:
        backend_options = getattr(self.spec, "backend_options", None)
        if isinstance(backend_options, dict):
            self.spec.backend_options = sanitize_public_outputs(backend_options) or {}
        return self


class SubModelOut(LinkedModel):
    """Wire shape of a SubModel row.

    A SubModel is one disconnected component within a Reconstruction
    (``sparse/0``, ``sparse/1``, ...). ``idx`` is the COLMAP-assigned
    component index; ``parent_submodel_id`` points at the source when
    the model came out of a hierarchical merge / split. ``summary``
    carries per-component stats (image count, point count, mean
    reprojection error) so collection endpoints don't need to crack
    the snapshot. ``snapshot_seq`` identifies the sealed snapshot;
    clients read points / cameras / images through the links rather
    than receiving server filesystem paths.
    """

    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

    submodel_id: str
    recon_id: str
    idx: int
    parent_submodel_id: str | None = None
    summary: dict | None = Field(default=None, validation_alias="summary_json")
    rigidity: dict | None = Field(default=None, validation_alias="rigidity_json")
    snapshot_seq: int | None = None
    sealed_path: str | None = None
    created_at: datetime
    links: dict[str, Link | None] | None = Field(default=None, alias="_links")

    @model_validator(mode="after")
    def _hide_internal_path(self) -> SubModelOut:
        self.sealed_path = None
        return self


class SnapshotListResponse(BaseModel):
    """``GET /v1/reconstructions/{recon_id}/snapshots`` envelope."""

    model_config = ConfigDict(populate_by_name=True)

    seqs: list[int] = Field(default_factory=list)
    # ``_links`` is HAL-shaped: a dict keyed by sequence number (as
    # str) plus a ``"latest"`` shortcut. Each value is itself a dict
    # of `{rel_name: {"href": "..."}}` — too dynamic to type strictly,
    # so we keep ``dict[str, Any]`` here.
    links: dict[str, Any] | None = Field(default=None, alias="_links")


class ImageObservationRow(BaseModel):
    """One observation of a 3D point in one image, served from
    ``observations_by_image.json``.

    ``point3d_id`` is the COLMAP-assigned 3D point id; ``kp_idx`` is the
    index into the image's keypoint list (matches ``ImagePose.points2D``);
    ``x`` / ``y`` are the 2D pixel coordinates (origin = top-left,
    EXIF-oriented); ``error`` is the per-residual reprojection error in
    pixels (``None`` when not produced by the worker).
    """

    point3d_id: int
    kp_idx: int
    x: float
    y: float
    error: float | None = None


class PointObservationRow(BaseModel):
    """One observation of a 3D point from one image, served from
    ``observations_by_point.json``.

    Mirror of :class:`ImageObservationRow` keyed on ``image_id``
    instead of ``point3d_id`` (different join order)."""

    image_id: int
    kp_idx: int
    x: float
    y: float
    error: float | None = None


class ImageObservationsResponse(BaseModel):
    """``GET .../images/{image_id}/observations`` envelope."""

    image_id: str
    observations: list[ImageObservationRow] = Field(default_factory=list)
    count: int = 0


class PointVisibilityResponse(BaseModel):
    """``GET .../points/{point3d_id}/visibility`` envelope."""

    point3d_id: str
    observations: list[PointObservationRow] = Field(default_factory=list)
    count: int = 0
