"""Project request/response schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from sceneapi.server.schemas.api.common import Link, Page, TimestampedModel


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(None, max_length=1024)


class ProjectPatch(BaseModel):
    """Partial update. Unset fields are left untouched."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=1024)


class ProjectOut(TimestampedModel):
    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

    project_id: str
    tenant_id: str
    name: str
    description: str | None = None
    updated_at: datetime | None = None
    links: dict[str, Link | None] | None = Field(default=None, alias="_links")


ProjectListPage = Page[ProjectOut]
