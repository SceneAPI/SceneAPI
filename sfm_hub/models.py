"""Typed plugin manifest models for sfm_hub."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

RuntimeMode = Literal["uv", "docker", "external_tool"]
TrustTier = Literal["official", "verified", "community", "local"]

# A manifest is the contract a backend ships; malformed values here are
# invisible until install / discovery time, so validate the shapes up front.
_PROVIDER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_ENTRY_POINT_RE = re.compile(r"^[A-Za-z_][\w.]*:[A-Za-z_]\w*$")
_GITHUB_URL_RE = re.compile(r"^https://github\.com/[^/\s]+/[^/\s]+")


def _known_capabilities() -> frozenset[str]:
    """The canonical capability vocabulary, imported lazily.

    Late import: ``app.core.capabilities`` depends only on stdlib and never
    imports ``sfm_hub``, so this adds no import cycle — but keeping it inside
    the function avoids a module-load-time edge from the lower-level hub
    package up into ``app``.
    """
    from app.core.capabilities import ALL_KNOWN

    return ALL_KNOWN


class UvRuntime(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["git"] = "git"
    url: str
    ref: str = "main"
    package: str

    @field_validator("url")
    @classmethod
    def _url_is_github(cls, url: str) -> str:
        if not _GITHUB_URL_RE.match(url):
            raise ValueError(
                f"uv runtime url must be a https://github.com/<owner>/<repo> url: {url!r}"
            )
        return url


class DockerRuntime(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image: str | None = None
    build_context: str | None = None


class ExternalToolRuntime(BaseModel):
    model_config = ConfigDict(extra="forbid")

    executable_names: list[str] = Field(default_factory=list)
    env_vars: list[str] = Field(default_factory=list)
    version_args: list[str] = Field(default_factory=lambda: ["--version"])


class RuntimeModes(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uv: UvRuntime | None = None
    docker: DockerRuntime | None = None
    external_tool: ExternalToolRuntime | None = None

    def enabled_modes(self) -> list[RuntimeMode]:
        out: list[RuntimeMode] = []
        if self.uv is not None:
            out.append("uv")
        if self.docker is not None:
            out.append("docker")
        if self.external_tool is not None:
            out.append("external_tool")
        return out


class ProviderManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str
    display_name: str
    description: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    backend_actions: list[str] = Field(default_factory=list)
    priority_hint: int = 100

    @field_validator("provider_id")
    @classmethod
    def _provider_id_format(cls, provider_id: str) -> str:
        if not _PROVIDER_ID_RE.match(provider_id):
            raise ValueError(f"provider_id must match {_PROVIDER_ID_RE.pattern!r}: {provider_id!r}")
        return provider_id

    @field_validator("capabilities")
    @classmethod
    def _capabilities_are_known(cls, capabilities: list[str]) -> list[str]:
        unknown = sorted(set(capabilities) - _known_capabilities())
        if unknown:
            raise ValueError(
                f"provider declares capabilities not in the sfmapi vocabulary: {', '.join(unknown)}"
            )
        return capabilities


class LicenseInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    url: str | None = None


class UpstreamProject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    url: str
    license: str | None = None


class Compatibility(BaseModel):
    model_config = ConfigDict(extra="allow")

    sfmapi: str = ">=0.0.1"
    python: str | None = ">=3.12,<3.13"
    os: list[str] = Field(default_factory=lambda: ["windows", "linux", "macos"])
    cuda: str | None = None
    tool_versions: dict[str, str] = Field(default_factory=dict)


class Conformance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["not_run", "partial", "passing", "failing"] = "not_run"
    suite: str | None = None
    report_url: str | None = None
    checked_at: str | None = None


class PluginManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plugin_id: str
    display_name: str
    description: str
    package_name: str
    github_url: str
    entry_points: list[str]
    providers: list[ProviderManifest]
    runtime_modes: RuntimeModes
    capabilities: list[str] = Field(default_factory=list)
    backend_actions: list[str] = Field(default_factory=list)
    config_schemas: list[str] = Field(default_factory=list)
    artifact_contracts: list[str] = Field(default_factory=list)
    licenses: list[LicenseInfo] = Field(default_factory=list)
    upstream_projects: list[UpstreamProject] = Field(default_factory=list)
    compatibility: Compatibility = Field(default_factory=Compatibility)
    conformance: Conformance = Field(default_factory=Conformance)
    trust_tier: TrustTier = "community"

    @field_validator("providers")
    @classmethod
    def _providers_are_unique(cls, providers: list[ProviderManifest]) -> list[ProviderManifest]:
        seen: set[str] = set()
        duplicates: list[str] = []
        for provider in providers:
            if provider.provider_id in seen:
                duplicates.append(provider.provider_id)
            seen.add(provider.provider_id)
        if duplicates:
            raise ValueError(f"duplicate provider ids: {', '.join(sorted(set(duplicates)))}")
        return providers

    @field_validator("capabilities")
    @classmethod
    def _capabilities_are_unique_and_known(cls, capabilities: list[str]) -> list[str]:
        unknown = sorted(set(capabilities) - _known_capabilities())
        if unknown:
            raise ValueError(
                f"manifest declares capabilities not in the sfmapi vocabulary: {', '.join(unknown)}"
            )
        return sorted(set(capabilities))

    @field_validator("github_url")
    @classmethod
    def _github_url_format(cls, github_url: str) -> str:
        if not _GITHUB_URL_RE.match(github_url):
            raise ValueError(
                f"github_url must be a https://github.com/<owner>/<repo> url: {github_url!r}"
            )
        return github_url

    @field_validator("entry_points")
    @classmethod
    def _entry_points_format(cls, entry_points: list[str]) -> list[str]:
        if not entry_points:
            raise ValueError("a plugin manifest must declare at least one entry point")
        bad = [ep for ep in entry_points if not _ENTRY_POINT_RE.match(ep)]
        if bad:
            raise ValueError(f"entry_points must use module:attr form: {', '.join(bad)}")
        return entry_points

    def runtime_mode_names(self) -> list[RuntimeMode]:
        return self.runtime_modes.enabled_modes()

    def provider_ids(self) -> list[str]:
        return [provider.provider_id for provider in self.providers]
