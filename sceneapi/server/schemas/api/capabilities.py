"""Wire envelope for ``GET /v1/capabilities``.

Mirrors :class:`sceneapi.server.core.capabilities.Capabilities` exactly —
declared here separately so FastAPI can advertise it in OpenAPI as
the typed response. SDK packages keep their own parallel definitions
in the separate ``sfmapi-sdk`` repository for the same reason
(independent release cadence + zero server-side import).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class BackendInfoOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    version: str
    vendor: str = ""


class CapabilitiesOut(BaseModel):
    """Snapshot of what the current deployment supports.

    ``schema_version`` tracks the wire envelope shape — independent
    of the feature flags themselves, which are negotiated via the
    ``features`` dict.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    backend: BackendInfoOut
    features: dict[str, bool] = Field(default_factory=dict)
