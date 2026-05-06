"""Shared API request/response models."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class Page(BaseModel, Generic[T]):
    """AIP-158 paginated list envelope. ``next_page_token`` is opaque
    and ``None`` when no further pages exist; ``total`` is optional
    (omitted when computing it would be expensive)."""

    items: list[T]
    next_page_token: str | None = None
    total: int | None = None


class HealthResponse(BaseModel):
    status: str = "ok"


class BackendVersion(BaseModel):
    """Backend identity + freeform engine version map.

    sfmapi has no concrete backend; whatever real backend is
    registered fills in its own ``runtime_versions`` keys (e.g.
    ``{"colmap_sha": "...", "cuda_arch": "120"}``). ``None`` when
    no backend is registered.
    """

    name: str
    version: str
    vendor: str | None = None
    runtime_versions: dict[str, str] = Field(default_factory=dict)


class VersionResponse(BaseModel):
    sfmapi: str
    backend: BackendVersion | None = None


class ReadyzResponse(BaseModel):
    """Readiness check envelope. ``status`` is ``"ok"`` when every
    backing store reports healthy; ``"degraded"`` when one or more
    are unreachable. ``checks`` carries a per-component status string."""

    status: str
    checks: dict[str, str] = Field(default_factory=dict)


class SpecServerInfo(BaseModel):
    name: str
    version: str


class SpecResponse(BaseModel):
    """Discovery envelope for ``GET /spec``. Identifies which standard
    this server implements so clients can pick a compatible SDK.

    ``spec_url`` defaults to the canonical GitHub Pages doc site;
    deployments may override via ``SFMAPI_SPEC_URL`` to point at a
    private mirror, or set it ``None`` to omit the field entirely.
    """

    spec: str
    spec_version: str
    spec_url: str | None = "https://sfmapi.github.io/spec"
    openapi_url: str
    server: SpecServerInfo


class ProblemResponse(BaseModel):
    """RFC 7807 ``application/problem+json`` envelope.

    Carries every key the server may emit (AIP-193). Optional fields:

    - ``errors`` — per-field Pydantic errors on a 422 (see L19);
      each entry has ``loc``, ``msg``, ``type`` and an optional
      ``input``.
    - ``capability`` — canonical capability name on a 501; pair with
      ``GET /v1/capabilities`` to discover what the deployment exposes.
    - ``retry_after`` — seconds to wait before retrying on a 429 / 503.
    """

    type: str
    title: str
    status: int
    detail: str | None = None
    instance: str | None = None
    errors: list[dict[str, Any]] | None = None
    capability: str | None = None
    retry_after: int | None = None


class Link(BaseModel):
    href: str | None = None


class HalLinks(BaseModel):
    """HAL-lite `_links` block. Each value points at a related resource."""

    model_config = ConfigDict(extra="allow")


class TimestampedModel(ORMModel):
    """Mixin for resources whose ORM row carries the canonical audit
    columns. ``updated_at`` is omitted from this base because not every
    table tracks it (jobs / submodels are append-only); resources that
    do (Project, Dataset) opt in by adding ``updated_at`` to their
    ``*Out`` schema."""

    created_at: datetime = Field(...)


class LinkedModel(ORMModel):
    """Mixin for resources that surface a `_links` block.

    The serialized JSON key is `_links` (alias) so the wire shape
    matches HAL conventions; in Python we name the attribute `links`
    because `_links` would be private-by-convention.
    """

    links: dict[str, Link | None] | None = Field(default=None, alias="_links")

    model_config = ConfigDict(populate_by_name=True, from_attributes=True)


def to_out[M: BaseModel](
    model_cls: type[M], obj: Any, *, links: dict[str, Link] | None = None
) -> M:
    """Validate `obj` (typically an ORM row) into `model_cls` and attach
    a `_links` HAL block in one step. Centralizes the 'load row, decorate
    with hypermedia links' pattern shared by every collection endpoint."""
    m = model_cls.model_validate(obj)
    return m if links is None else m.model_copy(update={"links": links})


__all__ = [
    "HalLinks",
    "HealthResponse",
    "Link",
    "LinkedModel",
    "ORMModel",
    "Page",
    "ProblemResponse",
    "ReadyzResponse",
    "SpecResponse",
    "SpecServerInfo",
    "TimestampedModel",
    "VersionResponse",
    "to_out",
]
