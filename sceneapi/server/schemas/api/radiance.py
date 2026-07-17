"""Radiance-field / 3DGS resource schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sceneapi.server.core.public_outputs import sanitize_public_error, sanitize_public_outputs
from sceneapi.server.schemas.api.common import Link, LinkedModel
from sceneapi.server.schemas.pipeline_spec import (
    PROVIDER_SELECTOR_MAX_LENGTH,
    PROVIDER_SELECTOR_PATTERN,
)

RadianceFieldStatus = Literal[
    "running",
    "succeeded",
    "failed",
    "cancelled",
    "cancelled_dirty",
]
RadianceEvaluationStatus = Literal[
    "running",
    "succeeded",
    "failed",
    "cancelled",
    "cancelled_dirty",
]
RadianceEvalMetric = Literal["psnr", "ssim", "lpips"]
RadianceEvalSplit = Literal["train", "val", "test", "all"]
RadianceLpipsNet = Literal["alex", "vgg", "squeeze"]
RadianceEvalBackground = Literal["dataset", "black", "white", "random"]


def _public_dict(value: Any) -> dict[str, Any]:
    sanitized = sanitize_public_outputs(value or {})
    return sanitized if isinstance(sanitized, dict) else {}


def _public_artifact_list(value: Any) -> list[dict[str, Any]]:
    sanitized = sanitize_public_outputs({"artifacts": value or []})
    artifacts = sanitized.get("artifacts") if isinstance(sanitized, dict) else []
    return (
        [item for item in artifacts if isinstance(item, dict)]
        if isinstance(artifacts, list)
        else []
    )


class RadianceEvalConfig(BaseModel):
    """Portable evaluation settings for splat providers.

    Provider-specific eval knobs stay in ``backend_options``; this shape
    captures the stable cross-provider contract exposed through the SDK.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    enabled: bool = False
    split: RadianceEvalSplit = "test"
    every_steps: int | None = Field(default=None, ge=1)
    final: bool = True
    metrics: list[RadianceEvalMetric] = Field(
        default_factory=lambda: ["psnr", "ssim", "lpips"],
        min_length=1,
    )
    max_images: int | None = Field(default=None, ge=1)
    image_downscale: int = Field(default=1, ge=1)
    crop_border_px: int = Field(default=0, ge=0)
    save_images: bool = False
    lpips_net: RadianceLpipsNet = "alex"
    background: RadianceEvalBackground = "dataset"


class RadianceMetrics(BaseModel):
    """Canonical aggregate radiance evaluation metrics."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    psnr_db: float | None = None
    ssim: float | None = None
    lpips: float | None = None
    num_images: int = Field(default=0, ge=0)
    duration_s: float | None = Field(default=None, ge=0)
    render_time_s_total: float | None = Field(default=None, ge=0)
    render_time_s_mean: float | None = Field(default=None, ge=0)


class RadianceTrainRequest(BaseModel):
    """Request body for ``POST /v1/projects/{project_id}/radiance_fields:train``.

    The alpha core implementation supports a deterministic ``stub`` provider
    for parity tests. Real training providers belong in plugins and should
    preserve provider-specific knobs inside ``backend_options``.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    dataset_id: str | None = Field(default=None, min_length=1, max_length=64)
    recon_id: str | None = Field(default=None, min_length=1, max_length=64)
    provider: str | None = Field(
        default=None,
        min_length=1,
        max_length=PROVIDER_SELECTOR_MAX_LENGTH,
        pattern=PROVIDER_SELECTOR_PATTERN,
    )
    method: str = Field(default="stub", min_length=1, max_length=64)
    max_steps: int = Field(default=1, ge=1, le=10_000_000)
    eval: RadianceEvalConfig | None = None
    backend_options: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=36,
        description="Client retry token. Reserved for idempotent radiance submissions.",
    )

    @model_validator(mode="after")
    def _one_input(self) -> RadianceTrainRequest:
        if bool(self.dataset_id) == bool(self.recon_id):
            raise ValueError("exactly one of dataset_id or recon_id is required")
        return self

    def spec(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "method": self.method,
            "max_steps": self.max_steps,
            **({"eval": self.eval.model_dump(mode="json")} if self.eval is not None else {}),
            "backend_options": dict(self.backend_options),
            "request_id": self.request_id,
        }
        if self.provider is not None:
            payload["provider"] = self.provider
        return payload


class RadianceEvaluateRequest(BaseModel):
    """Request body for ``POST /v1/radiance_fields/{id}:evaluate``."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    snapshot_seq: int | None = Field(default=None, ge=1)
    dataset_id: str | None = Field(default=None, min_length=1, max_length=64)
    provider: str | None = Field(
        default=None,
        min_length=1,
        max_length=PROVIDER_SELECTOR_MAX_LENGTH,
        pattern=PROVIDER_SELECTOR_PATTERN,
    )
    method: str | None = Field(default=None, min_length=1, max_length=64)
    eval: RadianceEvalConfig = Field(default_factory=lambda: RadianceEvalConfig(enabled=True))
    backend_options: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = Field(default=None, min_length=1, max_length=36)

    @model_validator(mode="after")
    def _force_eval_enabled(self) -> RadianceEvaluateRequest:
        self.eval.enabled = True
        return self


class RadianceFieldOut(LinkedModel):
    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

    radiance_field_id: str
    project_id: str
    dataset_id: str | None = None
    recon_id: str | None = None
    name: str
    provider: str
    method: str
    status: RadianceFieldStatus
    spec: dict[str, Any] = Field(..., validation_alias="spec_json")
    summary: dict[str, Any] | None = Field(default=None, validation_alias="summary_json")
    created_at: datetime
    updated_at: datetime
    links: dict[str, Link | None] | None = Field(default=None, alias="_links")

    @model_validator(mode="after")
    def _sanitize_public_fields(self) -> RadianceFieldOut:
        self.spec = _public_dict(self.spec)
        if self.summary is not None:
            self.summary = _public_dict(self.summary)
        return self


class RadianceSnapshotOut(LinkedModel):
    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

    snapshot_id: str
    radiance_field_id: str
    seq: int
    summary: dict[str, Any] | None = Field(default=None, validation_alias="summary_json")
    created_at: datetime
    links: dict[str, Link | None] | None = Field(default=None, alias="_links")

    @model_validator(mode="after")
    def _sanitize_public_summary(self) -> RadianceSnapshotOut:
        if self.summary is not None:
            self.summary = _public_dict(self.summary)
        return self


class RadianceEvaluationOut(LinkedModel):
    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

    evaluation_id: str
    radiance_field_id: str
    snapshot_seq: int
    dataset_id: str | None = None
    provider: str
    method: str
    split: str
    status: RadianceEvaluationStatus
    config: dict[str, Any] = Field(..., validation_alias="config_json")
    metrics: RadianceMetrics | None = Field(default=None, validation_alias="metrics_json")
    artifacts: list[dict[str, Any]] | None = Field(default=None, validation_alias="artifacts_json")
    error: dict[str, Any] | None = Field(default=None, validation_alias="error_json")
    job_id: str | None = None
    created_at: datetime
    updated_at: datetime
    links: dict[str, Link | None] | None = Field(default=None, alias="_links")

    @model_validator(mode="after")
    def _sanitize_public_fields(self) -> RadianceEvaluationOut:
        self.config = _public_dict(self.config)
        if self.metrics is not None:
            self.metrics = RadianceMetrics.model_validate(
                _public_dict(self.metrics.model_dump(mode="json"))
            )
        if self.artifacts is not None:
            self.artifacts = _public_artifact_list(self.artifacts)
        if self.error is not None:
            self.error = sanitize_public_error(self.error)
        return self


class RadianceVariantOut(LinkedModel):
    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

    variant_id: str
    snapshot_id: str
    format: str
    uri: str | None = None
    media_type: str | None = None
    summary: dict[str, Any] | None = Field(default=None, validation_alias="summary_json")
    created_at: datetime
    links: dict[str, Link | None] | None = Field(default=None, alias="_links")

    @model_validator(mode="after")
    def _sanitize_public_summary(self) -> RadianceVariantOut:
        if self.summary is not None:
            self.summary = _public_dict(self.summary)
        return self


class RadianceSnapshotListResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    seqs: list[int] = Field(default_factory=list)
    links: dict[str, Any] | None = Field(default=None, alias="_links")
