"""Stage artifact response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

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
    title: str
    description: str
    durable: bool


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
    summary: dict[str, Any] | None = Field(default=None, validation_alias="summary_json")
    metadata: dict[str, Any] | None = Field(default=None, validation_alias="metadata_json")
    created_at: datetime
    links: dict[str, Link | None] | None = Field(default=None, alias="_links")
