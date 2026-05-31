"""Stage artifact response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.api.common import Link, LinkedModel


class ArtifactRef(BaseModel):
    """Reference to a stage artifact used as a downstream stage input."""

    model_config = ConfigDict(extra="forbid")

    artifact_id: str = Field(..., min_length=1, max_length=64)
    kind: str | None = Field(
        default=None,
        min_length=1,
        max_length=96,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$",
        description="Optional expected artifact kind. The request fails if it does not match.",
    )


ArtifactInputMap = dict[str, ArtifactRef]


class ArtifactKindOut(BaseModel):
    """Documented core artifact kind."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    kind: str
    datatype: str
    title: str
    description: str
    durable: bool
    artifact_format: str = Field(description="Default canonical format id for this kind.")
    schema_version: int


class ArtifactFormatOut(BaseModel):
    """Documented core artifact interchange format."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    format_id: str
    datatype: str
    title: str
    description: str
    schema_version: int
    media_types: list[str]
    json_schema: dict[str, Any] | None = None
    examples: list[dict[str, Any]] = Field(default_factory=list)
    portable: bool = True


class ArtifactConversionOut(BaseModel):
    """Advertised conversion between artifact formats."""

    model_config = ConfigDict(extra="forbid")

    from_format: str
    to_format: str
    lossless: bool = False
    description: str | None = None


class ArtifactConversionPlanRequest(BaseModel):
    """Ask sfmapi to choose or validate an artifact format conversion."""

    model_config = ConfigDict(extra="forbid")

    provider: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
        description="Optional provider id to use when planning backend-native conversions.",
    )
    to_format: str | None = Field(
        default=None,
        min_length=1,
        max_length=96,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$",
        description="Exact target format id. Mutually compatible with accepted_formats.",
    )
    accepted_formats: list[str] = Field(
        default_factory=list,
        description="Acceptable target format ids in preference order.",
    )
    require_lossless: bool = False


class ArtifactConversionStepOut(BaseModel):
    """One conversion step selected from backend contracts."""

    model_config = ConfigDict(extra="forbid")

    contract_id: str | None = None
    backend: str | None = None
    provider: str | None = None
    from_format: str
    to_format: str
    lossless: bool = False
    description: str | None = None


class ArtifactConversionPlanOut(BaseModel):
    """Conversion compatibility result for an artifact."""

    model_config = ConfigDict(extra="forbid")

    artifact_id: str
    source_format: str | None = None
    target_format: str
    conversion_required: bool
    executable: bool
    reason: str | None = None
    steps: list[ArtifactConversionStepOut] = Field(default_factory=list)


class ArtifactConvertRequest(ArtifactConversionPlanRequest):
    """Submit a conversion job for one artifact."""

    name: str | None = Field(default=None, max_length=255)
    to_kind: str | None = Field(
        default=None,
        min_length=1,
        max_length=96,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$",
    )
    options: dict[str, Any] = Field(default_factory=dict)


class ArtifactFileRef(BaseModel):
    """One file that belongs to a portable artifact manifest."""

    model_config = ConfigDict(extra="forbid")

    name: str
    uri: str
    media_type: str | None = None
    sha256: str | None = Field(default=None, min_length=64, max_length=64)
    byte_size: int | None = Field(default=None, ge=0)


class ArtifactImportRequest(BaseModel):
    """Register an existing artifact URI as a typed sfmapi artifact.

    Imports do not copy bytes. They create a completed import job/task
    that owns the artifact descriptor so the artifact can be validated,
    converted, and used as a downstream stage input.
    """

    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(..., min_length=1, max_length=64)
    recon_id: str | None = Field(default=None, min_length=1, max_length=64)
    dataset_id: str | None = Field(default=None, min_length=1, max_length=64)
    kind: str = Field(
        ...,
        min_length=1,
        max_length=96,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$",
    )
    name: str | None = Field(default=None, max_length=255)
    uri: str | None = Field(default=None, max_length=2048)
    media_type: str | None = Field(default=None, max_length=127)
    artifact_format: str | None = Field(
        default=None,
        min_length=1,
        max_length=96,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$",
    )
    datatype: str | None = Field(
        default=None,
        min_length=1,
        max_length=96,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$",
    )
    schema_version: int | None = Field(default=None, ge=1)
    files: list[ArtifactFileRef] = Field(default_factory=list)
    sha256: str | None = Field(default=None, min_length=64, max_length=64)
    byte_size: int | None = Field(default=None, ge=0)
    coordinate_frame: str | None = Field(default=None, max_length=255)
    producer: dict[str, Any] | None = None
    summary: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ArtifactValidationIssueOut(BaseModel):
    """One artifact validation problem or warning."""

    model_config = ConfigDict(extra="forbid")

    level: str = Field(pattern=r"^(error|warning)$")
    field: str | None = None
    message: str


class ArtifactValidationOut(BaseModel):
    """Best-effort validation result for an artifact descriptor and bytes."""

    model_config = ConfigDict(extra="forbid")

    artifact_id: str
    valid: bool
    artifact_format: str | None = None
    datatype: str | None = None
    checked_content: bool = False
    issues: list[ArtifactValidationIssueOut] = Field(default_factory=list)


class StageArtifactOut(LinkedModel):
    """A typed worker output persisted independently of task logs.

    Unknown backends can emit multiple artifacts per stage. The API
    stores them here so clients can list and select exact outputs
    instead of guessing from the latest task dictionary.
    """

    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

    artifact_id: str
    job_id: str
    task_id: str
    recon_id: str | None = None
    dataset_id: str | None = None
    kind: str
    name: str | None = None
    uri: str | None = None
    media_type: str | None = None
    artifact_format: str | None = None
    datatype: str | None = None
    schema_version: int | None = None
    files: list[ArtifactFileRef] = Field(default_factory=list)
    sha256: str | None = Field(default=None, min_length=64, max_length=64)
    byte_size: int | None = Field(default=None, ge=0)
    coordinate_frame: str | None = None
    producer: dict[str, Any] | None = None
    summary: dict[str, Any] | None = Field(default=None, validation_alias="summary_json")
    metadata: dict[str, Any] | None = Field(default=None, validation_alias="metadata_json")
    created_at: datetime
    links: dict[str, Link | None] | None = Field(default=None, alias="_links")

    @model_validator(mode="after")
    def _lift_manifest_metadata(self) -> StageArtifactOut:
        metadata = self.metadata or {}
        for field_name in (
            "artifact_format",
            "datatype",
            "schema_version",
            "sha256",
            "byte_size",
            "coordinate_frame",
            "producer",
        ):
            if getattr(self, field_name) is None and field_name in metadata:
                setattr(self, field_name, metadata[field_name])
        if not self.files and isinstance(metadata.get("files"), list):
            self.files = [ArtifactFileRef.model_validate(item) for item in metadata["files"]]
        return self
