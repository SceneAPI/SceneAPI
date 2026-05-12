"""Wire schemas for ``/v1/oneshot/...`` — single-request,
no-persistence endpoints. Mirror of ``docs/guides/oneshot_streaming_proposal.md``.

These shapes are intentionally separate from the resource-API
schemas (``app/schemas/api/images.py`` etc.) because the one-shot
flow has no Image / Blob / Job rows backing it — the response is
the entire result, not a pointer at a persisted resource.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class OneShotImageInfo(BaseModel):
    """Header-derived image metadata echoed back for caller sanity checks.

    ``width`` and ``height`` are ``None`` when the server cannot read the
    dimensions cheaply from image headers. sfmapi does not decode pixels in
    the API layer.
    """

    width: int | None
    height: int | None
    byte_size: int


class OneShotFeaturesPayload(BaseModel):
    """The features themselves. ``keypoints`` is row-major
    ``[[x, y, scale, angle], ...]``; ``descriptors_b64`` is
    base64-encoded float32 descriptors of shape
    ``(count, descriptor_dim)`` in row-major order. ``descriptor_dim``
    is implied by the extractor (128 for SIFT)."""

    type: str
    count: int
    descriptor_dim: int
    keypoints: list[list[float]] = Field(default_factory=list)
    descriptors_b64: str = ""


class OneShotRuntimeInfo(BaseModel):
    backend: str
    ms: int


class OneShotFeaturesResponse(BaseModel):
    """``POST /v1/oneshot/features`` envelope. No persistence —
    everything the consumer needs is in this body. ``schema_version``
    versions the wire shape; bump on incompatible changes."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    kind: Literal["oneshot.features"] = "oneshot.features"
    image: OneShotImageInfo
    features: OneShotFeaturesPayload
    runtime: OneShotRuntimeInfo
    spec: dict[str, Any] = Field(default_factory=dict)


class OneShotLocalizeResponse(BaseModel):
    """``POST /v1/oneshot/localize`` envelope. Single-frame pose
    against an existing reconstruction with no DB row, no Job row,
    no upload step. The ``result`` field re-uses the existing
    :class:`~app.schemas.api.scene.LocalizationResult` shape verbatim
    so SDK consumers can re-decode through the typed model.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    kind: Literal["oneshot.localize"] = "oneshot.localize"
    recon_id: str
    image: OneShotImageInfo
    # ``result`` is :class:`LocalizationResult` validated as a dict
    # so this module can stay free of cross-import cycles.
    result: dict[str, Any]
    runtime: OneShotRuntimeInfo
    spec: dict[str, Any] = Field(default_factory=dict)
