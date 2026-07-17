"""Wire schemas for ``/v1/datasets/{id}/similarity[:build]``."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SimilarityNeighborOut(BaseModel):
    image_id: str
    distance: int


class SimilarityQueryResponse(BaseModel):
    """``GET /v1/datasets/{id}/similarity?image_id=...`` envelope."""

    query_image_id: str
    strategy: str
    k: int
    neighbors: list[SimilarityNeighborOut] = Field(default_factory=list)


class SimilarityBuildResponse(BaseModel):
    """``POST /v1/datasets/{id}/similarity:build`` synchronous build
    response (dhash). Async vlad path returns :class:`JobAcceptedResponse`."""

    strategy: str
    manifest_hash: str
    count: int
