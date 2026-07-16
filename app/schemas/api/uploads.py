"""Upload request/response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

UploadState = Literal["open", "received", "finalized", "expired"]
"""Closed set of upload lifecycle states (AIP-216).

- ``open`` — accepting ``PATCH`` chunks (``received_bytes <
  expected_size``)
- ``received`` — full payload uploaded; awaiting client ``finalize``
  (``received_bytes == expected_size``)
- ``finalized`` — sealed via ``POST /uploads/{id}:finalize``;
  ``blob_sha`` is set and the bytes are content-addressed
- ``expired`` — past ``expires_at`` and reaped by the janitor
"""


class UploadInit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_size: int = Field(..., gt=0)
    content_type: str | None = None
    expected_sha: str | None = Field(None, min_length=64, max_length=64)


class UploadFinalizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_sha: str | None = Field(None, min_length=64, max_length=64)


class UploadOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    upload_id: str
    state: UploadState
    expected_size: int
    received_bytes: int
    blob_sha: str | None = None
    expires_at: datetime
