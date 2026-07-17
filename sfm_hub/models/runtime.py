"""Provider runtime-mode models (uv / docker / container_service / external_tool)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from sfm_hub.models.validation import (
    _ENV_VAR_RE,
    _public_url_issue,
    _validate_github_url,
    _validate_public_build_args,
    _validate_public_env_mapping,
    _validate_public_https_url,
    _validate_public_image_ref,
    _validate_public_package_name,
    _validate_public_ref,
    _validate_public_relative_path,
    _validate_public_service_path,
)

RuntimeMode = Literal["uv", "docker", "container_service", "external_tool"]


class UvRuntime(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["git"] = "git"
    url: str
    ref: str = "main"
    package: str

    @field_validator("url")
    @classmethod
    def _url_is_github(cls, url: str) -> str:
        return _validate_github_url(
            url,
            label="uv runtime url",
        )

    @field_validator("ref")
    @classmethod
    def _ref_is_public(cls, ref: str) -> str:
        return _validate_public_ref(ref, label="uv runtime ref")

    @field_validator("package")
    @classmethod
    def _package_is_public(cls, package: str) -> str:
        return _validate_public_package_name(package, label="uv runtime package")


class DockerRuntime(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image: str | None = None
    build_context: str | None = None

    @field_validator("image")
    @classmethod
    def _image_is_public(cls, value: str | None) -> str | None:
        return _validate_public_image_ref(value, label="docker image")

    @field_validator("build_context")
    @classmethod
    def _build_context_is_public(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _validate_github_url(value, label="docker build context")


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
        return _validate_public_https_url(value, label="torch wheel index URLs")

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
    def _install_env_is_public(cls, value: dict[str, str]) -> dict[str, str]:
        return _validate_public_env_mapping(value, label="torch install env")


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
        issue = _public_url_issue(value, allowed_schemes={"http"})
        if issue is not None:
            raise ValueError(f"container service default_url {issue}")
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
        return _validate_public_service_path(
            value,
            label="container service healthcheck path",
        )


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

    @field_validator("dockerfile")
    @classmethod
    def _dockerfile_is_public_relative_path(cls, value: str) -> str:
        return _validate_public_relative_path(value, label="container service dockerfile")

    @model_validator(mode="after")
    def _build_source_is_public(self) -> ContainerServiceBuild:
        if self.source == "git":
            if self.context is not None:
                _validate_github_url(
                    self.context,
                    label="container service build context",
                )
            if self.ref is not None:
                _validate_public_ref(self.ref, label="container service build ref")
        elif self.source == "local":
            if self.context is not None:
                _validate_public_relative_path(
                    self.context,
                    label="container service build context",
                )
            if self.ref is not None:
                raise ValueError("container service local build ref is not allowed")
        elif self.source == "release":
            if self.context is not None:
                _validate_public_https_url(
                    self.context,
                    label="container service build context",
                )
            if self.ref is not None:
                raise ValueError("container service release build ref is not allowed")
        _validate_public_build_args(self.args)
        return self


class ContainerServiceImage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image: str | None = None
    digest: str | None = None
    registry: str | None = None
    build: ContainerServiceBuild | None = None

    @field_validator("image")
    @classmethod
    def _image_is_public(cls, value: str | None) -> str | None:
        return _validate_public_image_ref(value, label="container service image")

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

    @field_validator("sbom_url", "attestation_url")
    @classmethod
    def _url_is_public_https(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _validate_public_https_url(
            value,
            label="container service provenance URLs",
        )


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
        return _validate_public_service_path(
            value,
            label="container service execution path",
        )

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
