"""Backend extension action and config-schema schemas.

Backend actions are intentionally separate from sceneapi's portable
capability flags. They describe backend-native operations such as
COLMAP CLI commands, OpenMVG tools, diagnostics, or vendor extensions
that can still run through sfmapi jobs.

Backend config schemas describe valid ``backend_options`` keys for
portable sfmapi stages.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from sceneapi.server.schemas.api.artifacts import ArtifactConversionOut
from sceneapi.server.schemas.api.common import Link, Page
from sceneapi.server.schemas.pipeline_spec import (
    PROVIDER_SELECTOR_MAX_LENGTH,
    PROVIDER_SELECTOR_PATTERN,
)

BackendActionStability = Literal[
    "stable",
    "experimental",
    "backend_extension",
    "deprecated",
]
BackendActionSideEffects = Literal["none", "read", "write", "unknown"]


class BackendActionOut(BaseModel):
    """Discoverable backend-native operation.

    ``action_id`` is namespaced by the backend or tool family, for
    example ``colmap.feature_extractor``. Portable clients should treat
    action ids as opaque strings and inspect ``input_schema`` before
    presenting a UI or constructing a request.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    action_id: str
    backend: str
    display_name: str
    description: str | None = None
    category: str | None = None
    stability: BackendActionStability = "backend_extension"
    side_effects: BackendActionSideEffects = "unknown"
    long_running: bool = True
    supports_progress: bool = False
    idempotent: bool = False
    gpu_required: bool = True
    required_capabilities: list[str] = Field(default_factory=list)
    input_schema: dict[str, Any] | None = None
    output_schema: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    links: dict[str, Link | None] | None = Field(default=None, alias="_links")


BackendActionListPage = Page[BackendActionOut]


class BackendConfigSchemaOut(BaseModel):
    """Discoverable backend-specific options for a portable sfmapi stage.

    Clients send these settings in the stage spec's ``backend_options``
    object. Portable knobs stay on the top-level stage spec.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    config_id: str
    backend: str
    stage: str
    capability: str | None = None
    provider: str | None = None
    display_name: str
    description: str | None = None
    option_schema: dict[str, Any] | None = None
    defaults: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    links: dict[str, Link | None] | None = Field(default=None, alias="_links")


BackendConfigSchemaListPage = Page[BackendConfigSchemaOut]


class BackendArtifactContractOut(BaseModel):
    """Discoverable artifact input/output contract for a portable stage."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    contract_id: str
    backend: str
    stage: str
    capability: str | None = None
    provider: str | None = None
    display_name: str
    description: str | None = None
    accepts: list[str] = Field(default_factory=list)
    emits: list[str] = Field(default_factory=list)
    accepts_formats: list[str] = Field(default_factory=list)
    emits_formats: list[str] = Field(default_factory=list)
    preferred: str | None = None
    preferred_format: str | None = None
    conversions: list[ArtifactConversionOut] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    links: dict[str, Link | None] | None = Field(default=None, alias="_links")


BackendArtifactContractListPage = Page[BackendArtifactContractOut]


class BackendOut(BaseModel):
    """Active backend summary plus extension-action availability."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str
    version: str
    vendor: str = ""
    runtime_versions: dict[str, str] = Field(default_factory=dict)
    action_count: int = 0
    config_schema_count: int = 0
    artifact_contract_count: int = 0
    links: dict[str, Link | None] | None = Field(default=None, alias="_links")


class BackendActionValidateRequest(BaseModel):
    """Validate action input without submitting work."""

    model_config = ConfigDict(extra="forbid")

    project_id: str | None = Field(default=None, min_length=1)
    provider: str | None = Field(
        default=None,
        min_length=1,
        max_length=PROVIDER_SELECTOR_MAX_LENGTH,
        pattern=PROVIDER_SELECTOR_PATTERN,
    )
    inputs: dict[str, Any] = Field(default_factory=dict)


class BackendActionValidationErrorOut(BaseModel):
    field: str | None = None
    message: str


class BackendActionValidateResponse(BaseModel):
    action_id: str
    valid: bool
    errors: list[BackendActionValidationErrorOut] = Field(default_factory=list)
    normalized_inputs: dict[str, Any] = Field(default_factory=dict)


class BackendActionRunRequest(BaseModel):
    """Submit a backend-native action as an sfmapi job."""

    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(..., min_length=1)
    provider: str | None = Field(
        default=None,
        min_length=1,
        max_length=PROVIDER_SELECTOR_MAX_LENGTH,
        pattern=PROVIDER_SELECTOR_PATTERN,
    )
    inputs: dict[str, Any] = Field(default_factory=dict)
