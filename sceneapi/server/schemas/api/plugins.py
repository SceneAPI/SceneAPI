"""Plugin hub API schemas."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from sceneapi.server.schemas.api.common import Link, Page
from sfm_hub.doctor import DoctorCheck, ToolDetection
from sfm_hub.models import PluginManifest
from sfm_hub.state import RoutingProfile


class PluginRegistryItemOut(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    plugin_id: str
    display_name: str
    description: str
    package_name: str
    github_url: str
    trust_tier: str
    runtime_modes: list[str]
    providers: list[str]
    installed: bool = False
    enabled: bool = False
    links: dict[str, Link | None] | None = Field(default=None, alias="_links")


PluginRegistryPage = Page[PluginRegistryItemOut]


class PluginEntryPointOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plugin_id: str
    entry_point: str
    distribution: str | None = None
    version: str | None = None
    manifest: PluginManifest | None = None
    load_error: str | None = None


PluginEntryPointPage = Page[PluginEntryPointOut]


class PluginDetailOut(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    manifest: PluginManifest
    installed: bool = False
    enabled: bool = False
    links: dict[str, Link | None] | None = Field(default=None, alias="_links")


class PluginInstallRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: Literal["uv", "docker", "container_service", "external_tool"] = "uv"
    github_url: str | None = None
    ref: str | None = None
    package_name: str | None = None
    dry_run: bool = True
    allow_unsafe_execution: bool = False
    request_id: str | None = Field(
        default=None,
        pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$",
    )
    """Optional UUID-style idempotency key for retrying non-dry-run installs."""
    provision_runtime: bool = True
    """Run the installed plugin package's runtime provisioner when available.

    Provisioners are plugin-owned hooks for downloading release assets, cloning
    upstream engines, or building native tools after the Python package is
    installed. Use ``false`` to install only the wrapper package.
    """
    force: bool = False
    """Install even when the manifest's declared host compatibility
    (os / python) does not match this host. Mismatches still ride back
    in ``warnings``."""


class PluginProvisionStepOut(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str | None = None
    action: str | None = None
    status: str | None = None


class PluginProvisioningOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available: bool = False
    provisioned: bool = False
    steps: list[PluginProvisionStepOut] = Field(default_factory=list)
    env_keys: list[str] = Field(default_factory=list)
    redacted_env: dict[str, str] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PluginInstallResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plugin_id: str
    method: Literal["uv", "docker", "container_service", "external_tool"]
    dry_run: bool
    installed: bool
    command: list[str] = Field(default_factory=list)
    direct_reference: str | None = None
    warnings: list[str] = Field(default_factory=list)
    resolved_commit: str | None = None
    provision_runtime: bool = False
    provisioned: bool = False
    provisioning_status: str = "not_requested"
    provisioning_error: str | None = None
    request_id: str | None = None
    provisioning: PluginProvisioningOut | None = None


class PluginDoctorOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plugin_id: str
    status: Literal["pass", "warn", "fail"]
    checks: list[DoctorCheck] = Field(default_factory=list)


class ToolDetectionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tools: dict[str, list[ToolDetection]]


class ProviderOut(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    provider_id: str
    plugin_id: str
    display_name: str
    description: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    backend_actions: list[str] = Field(default_factory=list)
    runtime_modes: list[str] = Field(default_factory=list)
    installed: bool = True
    enabled: bool = True
    links: dict[str, Link | None] | None = Field(default=None, alias="_links")


ProviderPage = Page[ProviderOut]


class RoutingOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_profile: str | None = None
    provider_priority: list[str] = Field(default_factory=list)
    profiles: dict[str, RoutingProfile] = Field(default_factory=dict)
    project_profiles: dict[str, str] = Field(default_factory=dict)
    workspace_profiles: dict[str, str] = Field(default_factory=dict)


class RoutingProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    routes: dict[str, list[str]] = Field(default_factory=dict)


class RoutingProfileAssignmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: str = Field(min_length=1)


class ProviderPriorityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    providers: list[str] = Field(default_factory=list)
