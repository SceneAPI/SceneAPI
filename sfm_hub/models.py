"""Typed plugin manifest models for sfm_hub."""

from __future__ import annotations

import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

RuntimeMode = Literal["uv", "docker", "container_service", "external_tool"]
TrustTier = Literal["official", "verified", "community", "local"]

# A manifest is the contract a backend ships; malformed values here are
# invisible until install / discovery time, so validate the shapes up front.
# Provider-id pattern lives in app.core.ids (single source); the other
# three are sfm_hub-specific and stay here.
_ENTRY_POINT_RE = re.compile(r"^[A-Za-z_][\w.]*:[A-Za-z_]\w*$")
_GITHUB_URL_RE = re.compile(r"^https://github\.com/[^/\s]+/[^/\s]+")
_ENV_VAR_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")


def _provider_id_re() -> re.Pattern[str]:
    """Late import: ``app.core.ids`` ships only stdlib but ``app`` and
    ``sfm_hub`` cross-import elsewhere, so resolve lazily to avoid
    Python's module-import-cycle serialization."""
    from app.core.ids import PROVIDER_ID_RE
    return PROVIDER_ID_RE


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


class TorchRuntime(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy: Literal["optional", "recommended", "required"] = "recommended"
    device: Literal["cpu", "cuda"] = "cuda"
    index_url: str = "https://download.pytorch.org/whl/cu128"
    cpu_index_url: str = "https://download.pytorch.org/whl/cpu"
    packages: list[str] = Field(default_factory=lambda: ["torch", "torchvision", "torchaudio"])
    install_env: dict[str, str] = Field(default_factory=dict)

    @field_validator("index_url", "cpu_index_url")
    @classmethod
    def _url_is_https(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("torch wheel index URLs must be https:// URLs")
        return value

    @field_validator("packages")
    @classmethod
    def _packages_are_non_empty_names(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("torch runtime packages must not be empty")
        bad = [
            item
            for item in value
            if not item or item != item.strip() or any(char.isspace() for char in item)
        ]
        if bad:
            raise ValueError(f"torch runtime packages must be package names: {bad!r}")
        return value

    @field_validator("install_env")
    @classmethod
    def _install_env_uses_env_names(cls, value: dict[str, str]) -> dict[str, str]:
        bad = [key for key in value if not _ENV_VAR_RE.match(key)]
        if bad:
            raise ValueError(f"torch install env names must match {_ENV_VAR_RE.pattern!r}")
        return value


class ContainerServiceEndpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_url: str | None = None
    url_env: str | None = None

    @model_validator(mode="after")
    def _has_endpoint(self) -> ContainerServiceEndpoint:
        if not self.default_url and not self.url_env:
            raise ValueError("container service requires default_url or url_env")
        return self

    @field_validator("default_url")
    @classmethod
    def _url_is_http(cls, value: str | None) -> str | None:
        if value is None:
            return value
        parsed = urlsplit(value)
        if parsed.scheme != "http":
            raise ValueError("container service default_url must be http://")
        if not parsed.hostname:
            raise ValueError("container service default_url must include a host")
        if parsed.username or parsed.password:
            raise ValueError("container service default_url must not include credentials")
        if parsed.fragment:
            raise ValueError("container service default_url must not include a fragment")
        if parsed.query:
            raise ValueError("container service default_url must not include a query string")
        if any(char.isspace() for char in value):
            raise ValueError("container service default_url must not contain whitespace")
        return value

    @field_validator("url_env")
    @classmethod
    def _url_env_format(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not _ENV_VAR_RE.match(value):
            raise ValueError(f"url_env must match {_ENV_VAR_RE.pattern!r}: {value!r}")
        return value


class ContainerServiceHealthcheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = "/healthz"
    timeout_seconds: int = Field(5, ge=1)

    @field_validator("path")
    @classmethod
    def _path_is_non_empty(cls, value: str) -> str:
        if not value:
            raise ValueError("container service healthcheck path must be non-empty")
        return value


class ContainerServiceMounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_path: str = "/sfmapi/input"
    output_path: str = "/sfmapi/output"
    work_path: str = "/sfmapi/work"
    log_path: str = "/sfmapi/logs"

    @field_validator("input_path", "output_path", "work_path", "log_path")
    @classmethod
    def _path_is_absolute(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("container service mount paths must be absolute")
        if any(char.isspace() for char in value):
            raise ValueError("container service mount paths must not contain whitespace")
        return value


class ContainerServiceRetry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_attempts: int = Field(1, ge=1)
    backoff_seconds: int = Field(0, ge=0)


class ContainerServiceBuild(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["git", "local", "release"] = "git"
    context: str | None = None
    dockerfile: str = "Dockerfile"
    ref: str | None = None
    args: dict[str, str] = Field(default_factory=dict)


class ContainerServiceImage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image: str | None = None
    digest: str | None = None
    registry: str | None = None
    build: ContainerServiceBuild | None = None

    @model_validator(mode="after")
    def _has_image_or_build(self) -> ContainerServiceImage:
        if not self.image and self.build is None:
            raise ValueError("container service image requires image or build")
        return self

    @field_validator("digest")
    @classmethod
    def _digest_is_sha256(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not value.startswith("sha256:") or len(value) != 71:
            raise ValueError("container service image digest must be sha256:<64 hex chars>")
        try:
            int(value.removeprefix("sha256:"), 16)
        except ValueError as exc:
            raise ValueError("container service image digest must be hexadecimal") from exc
        return value


class ContainerServiceObjectStore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url_env: str | None = None
    input_prefix: str | None = None
    output_prefix: str | None = None

    @field_validator("url_env")
    @classmethod
    def _url_env_format(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not _ENV_VAR_RE.match(value):
            raise ValueError(f"object store url_env must match {_ENV_VAR_RE.pattern!r}: {value!r}")
        return value


class ContainerServiceCache(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy: Literal["none", "read_only", "read_write"] = "none"
    scope: Literal["request", "plugin", "global"] = "request"
    path: str | None = None

    @field_validator("path")
    @classmethod
    def _path_is_absolute(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not value.startswith("/"):
            raise ValueError("container service cache path must be absolute")
        if any(char.isspace() for char in value):
            raise ValueError("container service cache path must not contain whitespace")
        return value


class ContainerServiceProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image_digest_required: bool = True
    sbom_url: str | None = None
    attestation_url: str | None = None
    source_revision: str | None = None


class ContainerServiceExecution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = "/execute"
    timeout_seconds: int = Field(3600, ge=1)
    mounts: ContainerServiceMounts = Field(default_factory=ContainerServiceMounts)
    gpu: Literal["none", "optional", "required"] = "optional"
    env: list[str] = Field(default_factory=list)
    secrets: list[str] = Field(default_factory=list)
    retry: ContainerServiceRetry = Field(default_factory=ContainerServiceRetry)
    shutdown_timeout_seconds: int = Field(10, ge=0)
    log_collection: Literal["stdout", "file", "both"] = "both"
    artifact_collection: bool = True

    @field_validator("path")
    @classmethod
    def _path_is_absolute(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("container service execution path must start with /")
        if any(char.isspace() for char in value):
            raise ValueError("container service execution path must not contain whitespace")
        return value

    @field_validator("env", "secrets")
    @classmethod
    def _names_are_env_vars(cls, values: list[str]) -> list[str]:
        bad = [value for value in values if not _ENV_VAR_RE.match(value)]
        if bad:
            raise ValueError(
                f"container service env/secret names must match {_ENV_VAR_RE.pattern!r}"
            )
        return values


class ContainerServiceRuntime(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol: Literal["sfmapi-plugin-http-v1"]
    protocol_version: str
    service: ContainerServiceEndpoint
    image: ContainerServiceImage | None = None
    object_store: ContainerServiceObjectStore | None = None
    cache: ContainerServiceCache = Field(default_factory=ContainerServiceCache)
    provenance: ContainerServiceProvenance = Field(default_factory=ContainerServiceProvenance)
    healthcheck: ContainerServiceHealthcheck = Field(default_factory=ContainerServiceHealthcheck)
    execution: ContainerServiceExecution = Field(default_factory=ContainerServiceExecution)


class ExternalToolRuntime(BaseModel):
    model_config = ConfigDict(extra="forbid")

    executable_names: list[str] = Field(default_factory=list)
    env_vars: list[str] = Field(default_factory=list)
    version_args: list[str] = Field(default_factory=lambda: ["--version"])


class RuntimeModes(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uv: UvRuntime | None = None
    docker: DockerRuntime | None = None
    container_service: ContainerServiceRuntime | None = None
    external_tool: ExternalToolRuntime | None = None

    def enabled_modes(self) -> list[RuntimeMode]:
        out: list[RuntimeMode] = []
        if self.uv is not None:
            out.append("uv")
        if self.docker is not None:
            out.append("docker")
        if self.container_service is not None:
            out.append("container_service")
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
        pattern = _provider_id_re()
        if not pattern.match(provider_id):
            raise ValueError(f"provider_id must match {pattern.pattern!r}: {provider_id!r}")
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
    torch: TorchRuntime | None = None
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
