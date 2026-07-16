"""Stage artifact response schemas."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, model_validator

from app.core.public_outputs import (
    sanitize_public_artifact_file_refs,
    sanitize_public_artifact_metadata_dict,
    sanitize_public_artifact_name,
    sanitize_public_artifact_uri,
)
from app.schemas.api.common import Link, LinkedModel

_PROVIDER_SELECTOR_MAX_LENGTH = 129
_PROVIDER_SELECTOR_PATTERN = (
    r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}"
    r"(?:@[A-Za-z0-9][A-Za-z0-9_.-]{0,63})?$"
)
_ARTIFACT_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ArtifactFormatId = Annotated[
    str,
    Field(min_length=1, max_length=96, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$"),
]


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

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "if": {
                "properties": {
                    "to_format": {"type": "null"},
                },
            },
            "then": {
                "required": ["accepted_formats"],
                "properties": {
                    "accepted_formats": {"type": "array", "minItems": 1},
                },
            },
            "x-sfmapi-target-requirement": (
                "at least one of non-null to_format or non-empty accepted_formats is required"
            ),
        },
    )

    provider: str | None = Field(
        default=None,
        min_length=1,
        max_length=_PROVIDER_SELECTOR_MAX_LENGTH,
        pattern=_PROVIDER_SELECTOR_PATTERN,
        description="Optional provider id to use when planning backend-native conversions.",
    )
    to_format: str | None = Field(
        default=None,
        min_length=1,
        max_length=96,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$",
        description="Exact target format id. Mutually compatible with accepted_formats.",
    )
    accepted_formats: list[ArtifactFormatId] = Field(
        default_factory=list,
        min_length=1,
        description=(
            "Acceptable target format ids in preference order. Required to be "
            "non-empty when to_format is omitted."
        ),
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
    sha256: str | None = Field(
        default=None, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
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
    sha256: str | None = Field(
        default=None, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
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
    sha256: str | None = Field(
        default=None, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"
    )
    byte_size: int | None = Field(default=None, ge=0)
    coordinate_frame: str | None = None
    producer: dict[str, Any] | None = None
    summary: dict[str, Any] | None = Field(default=None, validation_alias="summary_json")
    metadata: dict[str, Any] | None = Field(default=None, validation_alias="metadata_json")
    created_at: datetime
    links: dict[str, Link | None] | None = Field(default=None, alias="_links")

    @model_validator(mode="after")
    def _lift_manifest_metadata(self, info: ValidationInfo) -> StageArtifactOut:
        content_href = ""
        content_path = ""
        if isinstance(info.context, dict):
            content_href = str(info.context.get("public_content_href") or "")
            content_path = str(info.context.get("public_content_path") or "")
        raw_metadata = self.metadata if isinstance(self.metadata, dict) else {}
        raw_metadata_files = raw_metadata.get("files")
        metadata = sanitize_public_artifact_metadata_dict(self.metadata) or {}
        self.metadata = metadata if self.metadata is not None else self.metadata
        if self.name is not None:
            self.name = sanitize_public_artifact_name(self.name)
        if self.uri is not None and not content_href:
            self.uri = sanitize_public_artifact_uri(self.uri)
        if self.summary is not None:
            self.summary = sanitize_public_artifact_metadata_dict(self.summary) or {}
        if self.producer is not None:
            self.producer = sanitize_public_artifact_metadata_dict(self.producer) or {}
        for field_name in ("artifact_format", "datatype"):
            value = metadata.get(field_name)
            if (
                getattr(self, field_name) is None
                and isinstance(value, str)
                and _ARTIFACT_KEY_RE.fullmatch(value)
            ):
                setattr(self, field_name, value)
        schema_version = metadata.get("schema_version")
        if (
            self.schema_version is None
            and isinstance(schema_version, int)
            and not isinstance(schema_version, bool)
            and schema_version >= 1
        ):
            self.schema_version = schema_version
        sha256 = metadata.get("sha256")
        if self.sha256 is None and isinstance(sha256, str) and _SHA256_RE.fullmatch(sha256):
            self.sha256 = sha256
        byte_size = metadata.get("byte_size")
        if (
            self.byte_size is None
            and isinstance(byte_size, int)
            and not isinstance(byte_size, bool)
            and byte_size >= 0
        ):
            self.byte_size = byte_size
        coordinate_frame = metadata.get("coordinate_frame")
        if (
            self.coordinate_frame is None
            and isinstance(coordinate_frame, str)
            and len(coordinate_frame) <= 255
        ):
            self.coordinate_frame = coordinate_frame
        producer = metadata.get("producer")
        if self.producer is None and isinstance(producer, dict):
            self.producer = sanitize_public_artifact_metadata_dict(producer) or {}
        raw_files = (
            [item.model_dump(mode="json") for item in self.files]
            if self.files
            else raw_metadata_files
        )
        public_files = sanitize_public_artifact_file_refs(
            raw_files,
            public_content_href=content_href,
            public_content_path=content_path,
        )
        if isinstance(raw_metadata_files, list):
            metadata["files"] = public_files
        else:
            metadata.pop("files", None)
        if public_files:
            files: list[ArtifactFileRef] = []
            for item in public_files:
                try:
                    files.append(ArtifactFileRef.model_validate(item))
                except ValueError:
                    continue
            self.files = files
        else:
            self.files = []
        return self
