"""Typed plugin manifest models for sfm_hub."""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Callable
from typing import Annotated, Any, Literal
from urllib.parse import unquote_plus, urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

RuntimeMode = Literal["uv", "docker", "container_service", "external_tool"]
TrustTier = Literal["official", "verified", "community", "local"]

# A manifest is the contract a backend ships; malformed values here are
# invisible until install / discovery time, so validate the shapes up front.
# Provider-id pattern lives in sfmapi.server.core.ids (single source); the other
# three are sfm_hub-specific and stay here.
_ENTRY_POINT_RE = re.compile(r"^[A-Za-z_][\w.]*:[A-Za-z_]\w*$")
_GITHUB_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_PUBLIC_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$")
_PUBLIC_PACKAGE_RE = re.compile(r"^[A-Za-z0-9_.-]+(?:\[[A-Za-z0-9_.-]+(?:,[A-Za-z0-9_.-]+)*\])?$")
_PUBLIC_IMAGE_REF_RE = re.compile(
    r"^(?P<registry>[a-z0-9](?:[a-z0-9.-]*[a-z0-9])(?::[0-9]{1,5})?)/"
    r"(?P<repository>[a-z0-9]+(?:[._-][a-z0-9]+)*"
    r"(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*)"
    r"(?::(?P<tag>[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}))?"
    r"(?:@sha256:[0-9a-fA-F]{64})?$"
)
_ENV_VAR_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_CONTRACT_ID_RE = re.compile(r"^[a-z][a-z0-9_.-]*$")
_LOCAL_DECLARATION_ID_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
_ROLE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_SPECIAL_ROLE_RE = re.compile(r"^[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)+$")
_ATTRIBUTE_RE = re.compile(r"^[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)*$")
_URL_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://[^\s,]+")
_SENSITIVE_PUBLIC_RE = re.compile(
    r"(token|secret|password|authorization|bearer|api[_-]?key|access[_-]?key|"
    r"client[_-]?secret|private[_-]?key|credential|signature|x-amz|"
    r"x-goog-signature|awsaccesskeyid|googleaccessid|sigv4|^sig$)",
    re.IGNORECASE,
)
_RESOLVER_ENV_KEYS = {
    "PIP_INDEX_URL",
    "PIP_EXTRA_INDEX_URL",
    "PIP_FIND_LINKS",
    "PIP_NO_INDEX",
    "PIP_CONFIG_FILE",
    "UV_INDEX",
    "UV_INDEX_URL",
    "UV_DEFAULT_INDEX",
    "UV_EXTRA_INDEX_URL",
    "UV_FIND_LINKS",
    "UV_NO_INDEX",
    "UV_INDEX_STRATEGY",
    "UV_KEYRING_PROVIDER",
    "UV_CONFIG_FILE",
    "UV_NO_CONFIG",
    "UV_NO_SYNC",
}
PROVIDER_SELECTOR_COMPONENT_MAX_LENGTH = 64
PROVIDER_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$"
CapabilityId = Annotated[str, Field(pattern=_CONTRACT_ID_RE.pattern)]


def _decoded_path_params(path: str) -> list[str]:
    from urllib.parse import unquote

    variants = [path]
    current = path
    for _ in range(2):
        decoded = unquote(current)
        if decoded == current:
            break
        if decoded not in variants:
            variants.append(decoded)
        current = decoded
    parts: list[str] = []
    for variant in variants:
        for delimiter in (";", "?", "#"):
            if delimiter not in variant:
                continue
            for part in re.split(r"[&;#]", variant.split(delimiter, 1)[1]):
                if part and part not in parts:
                    parts.append(part)
    return parts


def _public_url_issue(value: str, *, allowed_schemes: set[str]) -> str | None:
    if not value or value.strip() != value or any(char.isspace() for char in value):
        return "must not contain whitespace"
    parsed = urlsplit(value)
    if parsed.scheme not in allowed_schemes:
        return f"must be a {'/'.join(sorted(allowed_schemes))} URL"
    if not parsed.netloc:
        return "must include a host"
    if parsed.username or parsed.password:
        return "must not include credentials"
    if parsed.query or parsed.fragment:
        return "must not include query strings or fragments"
    for part in _decoded_path_params(parsed.path):
        key, _sep, item = part.partition("=")
        if _SENSITIVE_PUBLIC_RE.search(key) or _SENSITIVE_PUBLIC_RE.search(item):
            return "must not include signed path parameters"
    return None


def _validate_public_service_path(value: str, *, label: str) -> str:
    if not value:
        raise ValueError(f"{label} must be non-empty")
    if not value.startswith("/"):
        raise ValueError(f"{label} must start with /")
    if any(char.isspace() for char in value):
        raise ValueError(f"{label} must not contain whitespace")
    if "?" in value or "#" in value:
        raise ValueError(f"{label} must not include query strings or fragments")
    if "://" in value or "@" in value:
        raise ValueError(f"{label} must be a path, not a URL or authority")
    for part in _decoded_path_params(value):
        key, _sep, item = part.partition("=")
        if _SENSITIVE_PUBLIC_RE.search(key) or _SENSITIVE_PUBLIC_RE.search(item):
            raise ValueError(f"{label} must not include signed path parameters")
    return value


def _validate_public_https_url(value: str, *, label: str) -> str:
    issue = _public_url_issue(value, allowed_schemes={"https"})
    if issue is not None:
        raise ValueError(f"{label} {issue}")
    return value


def _validate_github_url(value: str, *, label: str) -> str:
    _validate_public_https_url(value, label=label)
    parsed = urlsplit(value)
    if parsed.netloc.lower() != "github.com":
        raise ValueError(f"{label} must be a https://github.com/<owner>/<repo> URL")
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) != 2:
        raise ValueError(f"{label} must identify a GitHub repository")
    owner, repo = parts[0], parts[1].removesuffix(".git")
    if not _GITHUB_NAME_RE.match(owner) or not _GITHUB_NAME_RE.match(repo):
        raise ValueError(f"{label} must include a valid GitHub owner and repository")
    return value


def _validate_public_ref(value: str, *, label: str) -> str:
    if (
        not _PUBLIC_REF_RE.match(value)
        or ".." in value.split("/")
        or _SENSITIVE_PUBLIC_RE.search(value)
    ):
        raise ValueError(f"{label} must be a public branch, tag, or commit")
    return value


def _validate_public_package_name(value: str, *, label: str) -> str:
    if not _PUBLIC_PACKAGE_RE.match(value) or _SENSITIVE_PUBLIC_RE.search(value):
        raise ValueError(f"{label} must be a public package name")
    return value


def _looks_like_local_path(value: str) -> bool:
    if not value:
        return False
    lower = value.lower()
    return (
        lower.startswith("file://")
        or value.startswith(("/", "\\"))
        or "\\" in value
        or (len(value) >= 3 and value[0].isalpha() and value[1] == ":" and value[2] in ("/", "\\"))
    )


def _validate_public_relative_path(value: str, *, label: str) -> str:
    if not value:
        raise ValueError(f"{label} must be non-empty")
    for variant in _public_text_variants(value):
        if (
            variant.strip() != variant
            or any(char.isspace() for char in variant)
            or variant.startswith(("/", "\\"))
            or "\\" in variant
            or "://" in variant
            or "@" in variant
            or "?" in variant
            or "#" in variant
            or _SENSITIVE_PUBLIC_RE.search(variant)
            or _looks_like_local_path(variant)
        ):
            raise ValueError(f"{label} must be a public relative path")
        parts = [part for part in variant.split("/") if part]
        if any(part == ".." for part in parts):
            raise ValueError(f"{label} must stay inside the build context")
    return value


def _private_registry_host(host: str) -> bool:
    normalized = host.split(":", 1)[0].rstrip(".").lower()
    if not normalized or "." not in normalized:
        return True
    if normalized in {
        "localhost",
        "host.docker.internal",
        "gateway.docker.internal",
    }:
        return True
    if (
        normalized.endswith(".localhost")
        or normalized.endswith(".local")
        or normalized.endswith(".internal")
    ):
        return True
    try:
        ip = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _validate_public_image_ref(value: str | None, *, label: str) -> str | None:
    if value is None:
        return value
    for variant in _public_text_variants(value):
        if (
            variant.strip() != variant
            or any(char.isspace() for char in variant)
            or "://" in variant
            or "\\" in variant
            or "?" in variant
            or "#" in variant
            or _looks_like_local_path(variant)
            or _SENSITIVE_PUBLIC_RE.search(variant)
        ):
            raise ValueError(f"{label} must be a public container image reference")
    match = _PUBLIC_IMAGE_REF_RE.match(value)
    if match is None or _private_registry_host(match.group("registry")):
        raise ValueError(f"{label} must use an explicit public registry image reference")
    return value


def _public_text_variants(text: str) -> list[str]:
    variants = [text]
    current = text
    for _ in range(2):
        if len(current) > 4096:
            break
        decoded = unquote_plus(current)
        if decoded == current:
            break
        if decoded not in variants:
            variants.append(decoded)
        current = decoded
    return variants


def _public_text_values(value: Any) -> list[str]:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return [str(value or "")]
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(_public_text_values(item))
        return out
    if isinstance(value, dict):
        out = []
        for item_key, item_value in value.items():
            out.append(str(item_key))
            out.extend(_public_text_values(item_value))
        return out
    raise ValueError("public extension values must be scalar, list, or object")


def _validate_public_env_mapping(args: dict[str, str], *, label: str) -> dict[str, str]:
    for key, value in args.items():
        if not _ENV_VAR_RE.match(key):
            raise ValueError(f"{label} names must be environment-style names")
        if key in _RESOLVER_ENV_KEYS or _SENSITIVE_PUBLIC_RE.search(key):
            raise ValueError(f"{label} must not contain secrets or resolver overrides")
        for variant in _public_text_variants(value):
            if _SENSITIVE_PUBLIC_RE.search(variant) or _looks_like_local_path(variant):
                raise ValueError(f"{label} must not contain secrets or local paths")
            for url in _URL_RE.findall(variant):
                issue = _public_url_issue(url, allowed_schemes={"http", "https"})
                if issue is not None:
                    raise ValueError(f"{label} URLs {issue}")
    return args


def _validate_public_build_args(args: dict[str, str]) -> dict[str, str]:
    return _validate_public_env_mapping(args, label="container service build args")


def _provider_id_re() -> re.Pattern[str]:
    """Late import: ``sfmapi.server.core.ids`` ships only stdlib but
    ``sfmapi.server`` and ``sfm_hub`` cross-import elsewhere, so resolve
    lazily to avoid Python's module-import-cycle serialization."""
    from sfmapi.server.core.ids import PROVIDER_ID_RE

    return PROVIDER_ID_RE


def _known_capabilities() -> frozenset[str]:
    """The canonical capability vocabulary, imported lazily.

    Late import: ``sfmapi.server.core.capabilities`` depends only on stdlib and never
    imports ``sfm_hub``, so this adds no import cycle — but keeping it inside
    the function avoids a module-load-time edge from the lower-level hub
    package up into ``app``.
    """
    from sfmapi.server.core.capabilities import ALL_KNOWN

    return ALL_KNOWN


def _core_datatype_ids() -> frozenset[str]:
    from sfmapi.server.core.datatypes import CORE_DATA_TYPES_BY_ID

    return frozenset(CORE_DATA_TYPES_BY_ID)


def _core_processor_ids() -> frozenset[str]:
    from sfmapi.server.core.processors import PROCESSORS_BY_ID

    return frozenset(PROCESSORS_BY_ID)


def _core_pipeline_ids() -> frozenset[str]:
    from sfmapi.server.core.pipelines import CANONICAL_PIPELINES

    return frozenset(CANONICAL_PIPELINES)


def _deny_core_ids_schema(
    ids_fn: Callable[[], frozenset[str]],
) -> Callable[[dict[str, Any]], None]:
    def _apply(schema: dict[str, Any]) -> None:
        try:
            forbidden = sorted(ids_fn())
        except Exception:  # pragma: no cover - schema generation diagnostic only
            return
        schema["not"] = {"enum": forbidden}

    return _apply


def _validate_plugin_owned_declaration_ids(
    *,
    plugin_id: str,
    datatypes: list[PluginDataTypeManifest],
    processors: list[PluginProcessorManifest],
    pipelines: list[PluginPipelineManifest],
) -> None:
    declarations = [
        *[("type_id", row.type_id) for row in datatypes],
        *[("processor_id", row.processor_id) for row in processors],
        *[("pipeline_id", row.pipeline_id) for row in pipelines],
    ]
    for field, value in declarations:
        if "." in value:
            raise ValueError(
                f"plugin-owned {field} {value!r} must be a local declaration id; "
                f"{plugin_id!r} is applied during registry merge"
            )


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


class ProviderManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str = Field(
        ...,
        max_length=PROVIDER_SELECTOR_COMPONENT_MAX_LENGTH,
        pattern=PROVIDER_ID_PATTERN,
    )
    display_name: str
    description: str | None = None
    capabilities: list[CapabilityId] = Field(default_factory=list)
    backend_actions: list[str] = Field(default_factory=list)
    priority_hint: int = 100

    @field_validator("provider_id")
    @classmethod
    def _provider_id_format(cls, provider_id: str) -> str:
        pattern = _provider_id_re()
        if not pattern.match(provider_id):
            raise ValueError(f"provider_id must match {pattern.pattern!r}: {provider_id!r}")
        if len(provider_id) > PROVIDER_SELECTOR_COMPONENT_MAX_LENGTH:
            raise ValueError(
                f"provider_id must be at most {PROVIDER_SELECTOR_COMPONENT_MAX_LENGTH} characters"
            )
        return provider_id

    @field_validator("capabilities")
    @classmethod
    def _capabilities_are_unique(cls, capabilities: list[str]) -> list[str]:
        return sorted(set(capabilities))


class PluginDataTypeManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type_id: str = Field(
        ...,
        pattern=_LOCAL_DECLARATION_ID_RE.pattern,
        json_schema_extra=_deny_core_ids_schema(_core_datatype_ids),
    )
    title: str
    kind: Literal["scene_input", "artifact"] = "artifact"
    description: str = ""

    @field_validator("type_id")
    @classmethod
    def _type_id_format(cls, type_id: str) -> str:
        if not _LOCAL_DECLARATION_ID_RE.match(type_id):
            raise ValueError(
                f"type_id must match {_LOCAL_DECLARATION_ID_RE.pattern!r}: {type_id!r}"
            )
        return type_id


class PluginPortSpecManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    datatype: str = Field(..., pattern=_CONTRACT_ID_RE.pattern)
    required: bool = True
    multiple: bool = False
    description: str = ""

    @field_validator("datatype")
    @classmethod
    def _datatype_format(cls, datatype: str) -> str:
        if not _CONTRACT_ID_RE.match(datatype):
            raise ValueError(f"datatype must match {_CONTRACT_ID_RE.pattern!r}: {datatype!r}")
        return datatype


class PluginAttributeManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., pattern=_ATTRIBUTE_RE.pattern)
    type: Literal["int", "float", "bool", "str", "enum", "datatype-ref", "object"]
    required: bool = False
    default: object | None = None
    enum: list[str] = Field(default_factory=list)
    min: int | float | None = None
    max: int | float | None = None
    description: str = ""

    @field_validator("name")
    @classmethod
    def _name_format(cls, name: str) -> str:
        if not _ATTRIBUTE_RE.match(name):
            raise ValueError(f"attribute name must match {_ATTRIBUTE_RE.pattern!r}: {name!r}")
        return name

    @model_validator(mode="after")
    def _enum_has_values(self) -> PluginAttributeManifest:
        if self.type == "enum" and not self.enum:
            raise ValueError("enum attributes must declare enum values")
        if self.enum and not all(isinstance(value, str) for value in self.enum):
            raise ValueError("attribute enum values must be strings")
        if self.required and "default" in self.model_fields_set:
            raise ValueError("attributes cannot be required and defaulted")
        return self


class PluginSpecialAttributeManifest(PluginAttributeManifest):
    name: str = Field(..., pattern=_SPECIAL_ROLE_RE.pattern)
    required: Literal[False] = False


class PluginProcessorManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    processor_id: str = Field(
        ...,
        pattern=_LOCAL_DECLARATION_ID_RE.pattern,
        json_schema_extra=_deny_core_ids_schema(_core_processor_ids),
    )
    title: str
    consumer: dict[str, PluginPortSpecManifest]
    supplier: dict[str, PluginPortSpecManifest]
    attributes: list[PluginAttributeManifest] = Field(default_factory=list)
    description: str = ""
    capabilities: list[CapabilityId] = Field(min_length=1)

    @field_validator("processor_id")
    @classmethod
    def _processor_id_format(cls, processor_id: str) -> str:
        if not _LOCAL_DECLARATION_ID_RE.match(processor_id):
            raise ValueError(
                f"processor_id must match {_LOCAL_DECLARATION_ID_RE.pattern!r}: {processor_id!r}"
            )
        return processor_id

    @field_validator("consumer", "supplier")
    @classmethod
    def _port_roles_format(
        cls,
        ports: dict[str, PluginPortSpecManifest],
    ) -> dict[str, PluginPortSpecManifest]:
        bad = [role for role in ports if not _ROLE_RE.match(role)]
        if bad:
            raise ValueError(
                f"port roles must match {_ROLE_RE.pattern!r}: {', '.join(sorted(bad))}"
            )
        return ports

    @field_validator("capabilities")
    @classmethod
    def _capabilities_are_unique(cls, capabilities: list[str]) -> list[str]:
        return sorted(set(capabilities))

    @model_validator(mode="after")
    def _attributes_unique(self) -> PluginProcessorManifest:
        names = [attr.name for attr in self.attributes]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f"duplicate processor attributes: {', '.join(duplicates)}")
        return self


class PluginSpecialInputPortSpecManifest(PluginPortSpecManifest):
    required: Literal[False] = False


class PluginProcessorExtensionManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    processor_id: str = Field(..., pattern=_CONTRACT_ID_RE.pattern)
    special_inputs: dict[
        Annotated[str, Field(pattern=_SPECIAL_ROLE_RE.pattern)],
        PluginSpecialInputPortSpecManifest,
    ] = Field(
        default_factory=dict,
        description=(
            "Plugin-qualified extension input roles. JSON Schema validates the "
            "qualified-name shape; runtime PluginManifest validation also "
            "requires the prefix to match plugin_id."
        ),
        json_schema_extra={
            "additionalProperties": False,
            "propertyNames": {"pattern": _SPECIAL_ROLE_RE.pattern},
        },
    )
    special_attributes: list[PluginSpecialAttributeManifest] = Field(
        default_factory=list,
        description=(
            "Plugin-qualified extension attributes. JSON Schema validates the "
            "qualified-name shape; runtime PluginManifest validation also "
            "requires the prefix to match plugin_id."
        ),
    )

    @field_validator("processor_id")
    @classmethod
    def _processor_id_format(cls, processor_id: str) -> str:
        if not _CONTRACT_ID_RE.match(processor_id):
            raise ValueError(
                f"processor_id must match {_CONTRACT_ID_RE.pattern!r}: {processor_id!r}"
            )
        return processor_id

    @field_validator("special_inputs")
    @classmethod
    def _special_input_roles_format(
        cls,
        ports: dict[str, PluginSpecialInputPortSpecManifest],
    ) -> dict[str, PluginSpecialInputPortSpecManifest]:
        bad = [role for role in ports if not _SPECIAL_ROLE_RE.match(role)]
        if bad:
            raise ValueError(
                "special input roles must be plugin-qualified and match "
                f"{_SPECIAL_ROLE_RE.pattern!r}: {', '.join(sorted(bad))}"
            )
        return ports

    @model_validator(mode="after")
    def _special_attributes_unique(self) -> PluginProcessorExtensionManifest:
        required_inputs = [role for role, port in self.special_inputs.items() if port.required]
        if required_inputs:
            raise ValueError(
                "special_inputs must be optional; set required=false for: "
                + ", ".join(sorted(required_inputs))
            )
        required_attributes = [attr.name for attr in self.special_attributes if attr.required]
        if required_attributes:
            raise ValueError(
                "special_attributes must be optional; set required=false for: "
                + ", ".join(sorted(required_attributes))
            )
        names = [attr.name for attr in self.special_attributes]
        unqualified = sorted(name for name in names if "." not in name)
        if unqualified:
            raise ValueError(
                "special attribute names must be plugin-qualified: " + ", ".join(unqualified)
            )
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f"duplicate special attributes: {', '.join(duplicates)}")
        return self


WireList = Annotated[
    list[str],
    Field(json_schema_extra={"uniqueItems": True}),
]


class PluginPipelineStepManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref: str = Field(..., pattern=_ROLE_RE.pattern)
    processor: str = Field(..., pattern=_CONTRACT_ID_RE.pattern)
    attributes: dict[str, object] = Field(default_factory=dict)
    wires: dict[str, str | WireList] = Field(default_factory=dict)

    @field_validator("ref")
    @classmethod
    def _ref_format(cls, ref: str) -> str:
        if not _ROLE_RE.match(ref):
            raise ValueError(f"step ref must match {_ROLE_RE.pattern!r}: {ref!r}")
        if ref == "inputs":
            raise ValueError("'inputs' is reserved for the synthetic pipeline input source")
        return ref

    @field_validator("processor")
    @classmethod
    def _processor_format(cls, processor: str) -> str:
        if not _CONTRACT_ID_RE.match(processor):
            raise ValueError(f"processor must match {_CONTRACT_ID_RE.pattern!r}: {processor!r}")
        return processor

    @field_validator("wires")
    @classmethod
    def _wire_arrays_are_unique(
        cls,
        wires: dict[str, str | list[str]],
    ) -> dict[str, str | list[str]]:
        for role, raw in wires.items():
            if not isinstance(raw, list):
                continue
            duplicates = sorted({value for value in raw if raw.count(value) > 1})
            if duplicates:
                raise ValueError(
                    f"wires.{role} must not contain duplicate supplier reference(s): "
                    + ", ".join(duplicates)
                )
        return wires


class PluginPipelineManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pipeline_id: str = Field(
        ...,
        pattern=_LOCAL_DECLARATION_ID_RE.pattern,
        json_schema_extra=_deny_core_ids_schema(_core_pipeline_ids),
    )
    title: str
    initial_inputs: list[Annotated[str, Field(pattern=_CONTRACT_ID_RE.pattern)]] = Field(
        default_factory=lambda: ["image_sequence"],
        min_length=1,
        json_schema_extra={"uniqueItems": True},
    )
    steps: list[PluginPipelineStepManifest] = Field(min_length=1)
    description: str = ""

    @field_validator("pipeline_id")
    @classmethod
    def _pipeline_id_format(cls, pipeline_id: str) -> str:
        if not _LOCAL_DECLARATION_ID_RE.match(pipeline_id):
            raise ValueError(
                f"pipeline_id must match {_LOCAL_DECLARATION_ID_RE.pattern!r}: {pipeline_id!r}"
            )
        return pipeline_id

    @field_validator("initial_inputs")
    @classmethod
    def _initial_inputs_format(cls, inputs: list[str]) -> list[str]:
        bad = [datatype for datatype in inputs if not _CONTRACT_ID_RE.match(datatype)]
        if bad:
            raise ValueError(
                f"initial_inputs must match {_CONTRACT_ID_RE.pattern!r}: " + ", ".join(sorted(bad))
            )
        duplicates = sorted({datatype for datatype in inputs if inputs.count(datatype) > 1})
        if duplicates:
            raise ValueError(
                "initial_inputs must not contain duplicate datatype(s): " + ", ".join(duplicates)
            )
        return inputs

    @model_validator(mode="after")
    def _refs_unique(self) -> PluginPipelineManifest:
        refs = [step.ref for step in self.steps]
        duplicates = sorted({ref for ref in refs if refs.count(ref) > 1})
        if duplicates:
            raise ValueError(f"duplicate pipeline step refs: {', '.join(duplicates)}")
        return self


def _core_processor_ports() -> dict[
    str,
    tuple[dict[str, object], dict[str, object]],
]:
    from sfmapi.server.core.processors import PROCESSORS_BY_ID

    return {
        processor_id: (processor.consumer, processor.supplier)
        for processor_id, processor in PROCESSORS_BY_ID.items()
    }


def _plugin_processor_ports(
    processors: list[PluginProcessorManifest],
) -> dict[str, tuple[dict[str, object], dict[str, object]]]:
    return {
        processor.processor_id: (processor.consumer, processor.supplier) for processor in processors
    }


def _core_processor_attributes() -> dict[str, list[PluginAttributeManifest]]:
    from sfmapi.server.core.processors import PROCESSORS_BY_ID

    return {
        processor_id: [
            PluginAttributeManifest.model_validate(attr.contract_dict())
            for attr in processor.attributes
        ]
        for processor_id, processor in PROCESSORS_BY_ID.items()
    }


def _plugin_processor_attributes(
    processors: list[PluginProcessorManifest],
) -> dict[str, list[PluginAttributeManifest]]:
    return {processor.processor_id: list(processor.attributes) for processor in processors}


def _wire_values(raw: object, *, multiple: bool) -> list[object]:
    if multiple:
        return list(raw) if isinstance(raw, list) else [raw]
    return [raw]


def _parse_wire_ref(value: object) -> tuple[str, str] | None:
    if not isinstance(value, str) or "." not in value:
        return None
    if value.startswith("inputs."):
        port = value.removeprefix("inputs.")
        return ("inputs", port) if port else None
    ref, port = value.rsplit(".", 1)
    if not ref or not port:
        return None
    return ref, port


def _value_matches_attribute(
    attr: PluginAttributeManifest,
    value: object,
    *,
    known_datatypes: set[str] | frozenset[str] | None = None,
) -> bool:
    if attr.type == "bool":
        return isinstance(value, bool)
    if attr.type == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if attr.type == "float":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if attr.type == "str":
        return isinstance(value, str)
    if attr.type == "enum":
        return value in attr.enum
    if attr.type == "datatype-ref":
        if not isinstance(value, str):
            return False
        if known_datatypes is not None:
            return value in known_datatypes
        return value in _core_datatype_ids()
    if attr.type == "object":
        return isinstance(value, dict)
    return False


def _validate_attribute_schema(
    attr: PluginAttributeManifest,
    *,
    known_datatypes: set[str] | frozenset[str],
) -> None:
    if attr.type != "enum" and attr.enum:
        raise ValueError(f"attribute {attr.name!r} uses enum values but type is {attr.type!r}")
    if attr.enum and not all(isinstance(value, str) for value in attr.enum):
        raise ValueError(f"attribute {attr.name!r} enum values must be strings")
    if attr.type == "enum" and len(set(map(repr, attr.enum))) != len(attr.enum):
        raise ValueError(f"attribute {attr.name!r} enum values must be unique")
    if attr.type not in {"int", "float"} and (attr.min is not None or attr.max is not None):
        raise ValueError(f"attribute {attr.name!r} min/max are only valid for numeric types")
    if attr.min is not None and attr.max is not None and attr.min > attr.max:
        raise ValueError(f"attribute {attr.name!r} min must be <= max")
    if "default" in attr.model_fields_set:
        if attr.default is None:
            raise ValueError(f"attribute {attr.name!r} default cannot be null")
        if not _value_matches_attribute(
            attr,
            attr.default,
            known_datatypes=known_datatypes,
        ):
            raise ValueError(f"attribute {attr.name!r} default must match type {attr.type!r}")
        if (
            attr.type in {"int", "float"}
            and isinstance(attr.default, (int, float))
            and not isinstance(attr.default, bool)
        ):
            if attr.min is not None and attr.default < attr.min:
                raise ValueError(f"attribute {attr.name!r} default must be >= min")
            if attr.max is not None and attr.default > attr.max:
                raise ValueError(f"attribute {attr.name!r} default must be <= max")


def _validate_step_attributes(
    *,
    pipeline_id: str,
    step: PluginPipelineStepManifest,
    attributes: list[PluginAttributeManifest],
    known_datatypes: set[str] | frozenset[str],
) -> None:
    by_name = {attr.name: attr for attr in attributes}
    unknown = sorted(set(step.attributes) - set(by_name))
    if unknown:
        raise ValueError(
            f"pipeline {pipeline_id!r} step {step.ref!r} uses unknown "
            f"attribute(s): {', '.join(unknown)}"
        )
    for attr in attributes:
        if attr.name not in step.attributes:
            if attr.required:
                raise ValueError(
                    f"pipeline {pipeline_id!r} step {step.ref!r} missing "
                    f"required attribute {attr.name!r}"
                )
            continue
        value = step.attributes[attr.name]
        if value is None or not _value_matches_attribute(
            attr,
            value,
            known_datatypes=known_datatypes,
        ):
            raise ValueError(
                f"pipeline {pipeline_id!r} step {step.ref!r} attribute "
                f"{attr.name!r} must be {attr.type}"
            )
        if attr.min is not None and isinstance(value, (int, float)) and value < attr.min:
            raise ValueError(
                f"pipeline {pipeline_id!r} step {step.ref!r} attribute "
                f"{attr.name!r} must be >= {attr.min}"
            )
        if attr.max is not None and isinstance(value, (int, float)) and value > attr.max:
            raise ValueError(
                f"pipeline {pipeline_id!r} step {step.ref!r} attribute "
                f"{attr.name!r} must be <= {attr.max}"
            )


def _requires_verified_match_graph(processor_id: str, role: str) -> bool:
    return processor_id in {"map", "triangulate"} and role == "matches"


def _validate_pipeline_graph(
    *,
    pipeline: PluginPipelineManifest,
    processor_ports: dict[str, tuple[dict[str, object], dict[str, object]]],
    processor_attributes: dict[str, list[PluginAttributeManifest]],
    known_datatypes: set[str] | frozenset[str],
) -> None:
    available: list[tuple[str, str, str, bool]] = [
        ("inputs", datatype, datatype, False) for datatype in pipeline.initial_inputs
    ]
    by_wire = {f"{ref}.{port}": (datatype, verified) for ref, port, datatype, verified in available}
    for step in pipeline.steps:
        ports = processor_ports.get(step.processor)
        if ports is None:
            continue
        _validate_step_attributes(
            pipeline_id=pipeline.pipeline_id,
            step=step,
            attributes=processor_attributes.get(step.processor, []),
            known_datatypes=known_datatypes,
        )
        consumer, supplier = ports
        unknown_wires = sorted(set(step.wires) - set(consumer))
        if unknown_wires:
            raise ValueError(
                f"pipeline {pipeline.pipeline_id!r} step {step.ref!r} wires "
                f"unknown consumer ports: {', '.join(unknown_wires)}"
            )
        for role, port in consumer.items():
            datatype = str(port.datatype)  # type: ignore[attr-defined]
            required = bool(port.required)  # type: ignore[attr-defined]
            multiple = bool(port.multiple)  # type: ignore[attr-defined]
            if role in step.wires:
                raw_wire = step.wires[role]
                if isinstance(raw_wire, list) and not multiple:
                    raise ValueError(
                        f"pipeline {pipeline.pipeline_id!r} step {step.ref!r} "
                        f"port {role!r} does not accept multiple inputs"
                    )
                values = _wire_values(raw_wire, multiple=multiple)
                if not values and required:
                    raise ValueError(
                        f"pipeline {pipeline.pipeline_id!r} step {step.ref!r} "
                        f"missing required input port {role!r}"
                    )
                wire_keys: list[str] = []
                for value in values:
                    parsed = _parse_wire_ref(value)
                    if parsed is None:
                        raise ValueError(
                            f"pipeline {pipeline.pipeline_id!r} step {step.ref!r} "
                            f"wire for port {role!r} must be 'step_ref.supplier_port'"
                        )
                    supplied = by_wire.get(f"{parsed[0]}.{parsed[1]}")
                    if supplied is None:
                        raise ValueError(
                            f"pipeline {pipeline.pipeline_id!r} step {step.ref!r} "
                            f"references unknown supplier port {parsed[0]}.{parsed[1]}"
                        )
                    supplied_datatype, supplied_verified = supplied
                    if supplied_datatype != datatype:
                        raise ValueError(
                            f"pipeline {pipeline.pipeline_id!r} step {step.ref!r} "
                            f"datatype mismatch for port {role!r}: expected "
                            f"{datatype}, got {supplied_datatype}"
                        )
                    if (
                        _requires_verified_match_graph(step.processor, role)
                        and not supplied_verified
                    ):
                        raise ValueError(
                            f"pipeline {pipeline.pipeline_id!r} step {step.ref!r} "
                            f"port {role!r} requires verified match_graph input"
                        )
                    wire_keys.append(f"{parsed[0]}.{parsed[1]}")
                distinct_wire_keys = set(wire_keys)
                if multiple and len(distinct_wire_keys) != len(wire_keys):
                    raise ValueError(
                        f"pipeline {pipeline.pipeline_id!r} step {step.ref!r} "
                        f"port {role!r} does not accept duplicate inputs"
                    )
                if multiple and required and len(distinct_wire_keys) < 2:
                    raise ValueError(
                        f"pipeline {pipeline.pipeline_id!r} step {step.ref!r} "
                        f"port {role!r} requires at least two distinct inputs"
                    )
                continue

            if not required:
                continue

            candidates = [s for s in available if s[2] == datatype]
            if not candidates and required:
                raise ValueError(
                    f"pipeline {pipeline.pipeline_id!r} step {step.ref!r} "
                    f"missing input datatype {datatype!r}"
                )
            if multiple and required and len({(s[0], s[1]) for s in candidates}) == 1:
                raise ValueError(
                    f"pipeline {pipeline.pipeline_id!r} step {step.ref!r} "
                    f"port {role!r} requires at least two distinct inputs"
                )
            if candidates and len(candidates) > 1 and not multiple:
                raise ValueError(
                    f"pipeline {pipeline.pipeline_id!r} step {step.ref!r} "
                    f"has ambiguous input for port {role!r}"
                )
            if _requires_verified_match_graph(step.processor, role) and not any(
                s[3] for s in candidates
            ):
                raise ValueError(
                    f"pipeline {pipeline.pipeline_id!r} step {step.ref!r} "
                    f"port {role!r} requires verified match_graph input"
                )
        for role, port in supplier.items():
            datatype = str(port.datatype)  # type: ignore[attr-defined]
            verified = (
                step.processor == "verify" and role == "matches" and datatype == "match_graph"
            )
            available.append((step.ref, role, datatype, verified))
            by_wire[f"{step.ref}.{role}"] = (datatype, verified)


def _validate_typed_extension_graph(
    *,
    datatypes: list[PluginDataTypeManifest],
    processors: list[PluginProcessorManifest],
    pipelines: list[PluginPipelineManifest],
    processor_extensions: list[PluginProcessorExtensionManifest],
    declared_capabilities: set[str] | None = None,
    provider_capability_sets: list[set[str]] | None = None,
    extension_namespace_prefixes: set[str] | None = None,
) -> None:
    datatype_ids = [dt.type_id for dt in datatypes]
    duplicate_dts = sorted({dt for dt in datatype_ids if datatype_ids.count(dt) > 1})
    if duplicate_dts:
        raise ValueError(f"duplicate datatypes: {', '.join(duplicate_dts)}")
    core_dts = _core_datatype_ids()
    core_shadow_dts = sorted(set(datatype_ids) & core_dts)
    if core_shadow_dts:
        raise ValueError(
            f"plugin datatypes cannot redefine core datatypes: {', '.join(core_shadow_dts)}"
        )

    processor_ids = [processor.processor_id for processor in processors]
    duplicate_processors = sorted({pid for pid in processor_ids if processor_ids.count(pid) > 1})
    if duplicate_processors:
        raise ValueError(f"duplicate processors: {', '.join(duplicate_processors)}")
    core_processors = _core_processor_ids()
    core_shadow_processors = sorted(set(processor_ids) & core_processors)
    if core_shadow_processors:
        raise ValueError(
            "plugin processors cannot redefine core processors; use "
            f"processor_extensions instead: {', '.join(core_shadow_processors)}"
        )

    if declared_capabilities is not None:
        for processor in processors:
            undeclared = sorted(set(processor.capabilities) - declared_capabilities)
            if undeclared:
                raise ValueError(
                    f"processor {processor.processor_id!r} references undeclared "
                    f"capabilities: {', '.join(undeclared)}"
                )
    if provider_capability_sets is not None:
        for processor in processors:
            required = set(processor.capabilities)
            if not any(required <= caps for caps in provider_capability_sets):
                raise ValueError(
                    f"processor {processor.processor_id!r} capabilities are not "
                    "declared together by any provider"
                )

    known_dts = core_dts | set(datatype_ids)
    for processor in processors:
        for attr in processor.attributes:
            _validate_attribute_schema(attr, known_datatypes=known_dts)
    for processor in processors:
        for role, port in {**processor.consumer, **processor.supplier}.items():
            if port.datatype not in known_dts:
                raise ValueError(
                    f"processor {processor.processor_id!r} port {role!r} "
                    f"references unknown datatype {port.datatype!r}"
                )
    known_processors = core_processors | set(processor_ids)
    extension_ids = [extension.processor_id for extension in processor_extensions]
    duplicate_extensions = sorted({pid for pid in extension_ids if extension_ids.count(pid) > 1})
    if duplicate_extensions:
        raise ValueError(f"duplicate processor_extensions: {', '.join(duplicate_extensions)}")
    for extension in processor_extensions:
        if extension.processor_id not in known_processors:
            raise ValueError(
                f"processor extension references unknown processor {extension.processor_id!r}"
            )
        for role, port in extension.special_inputs.items():
            if port.datatype not in known_dts:
                raise ValueError(
                    f"processor extension {extension.processor_id!r} "
                    f"input {role!r} references unknown datatype {port.datatype!r}"
                )
        for attr in extension.special_attributes:
            _validate_attribute_schema(attr, known_datatypes=known_dts)

    if extension_namespace_prefixes is not None:
        prefixes = tuple(f"{prefix}." for prefix in sorted(extension_namespace_prefixes))
        for extension in processor_extensions:
            special_input_names = sorted(extension.special_inputs)
            special_attribute_names = sorted(attr.name for attr in extension.special_attributes)
            bad = [
                name
                for name in [*special_input_names, *special_attribute_names]
                if not name.startswith(prefixes)
            ]
            if bad:
                raise ValueError(
                    "processor extension names must use the owning plugin namespace: "
                    + ", ".join(bad)
                )

    pipeline_ids = [pipeline.pipeline_id for pipeline in pipelines]
    duplicate_pipelines = sorted({pid for pid in pipeline_ids if pipeline_ids.count(pid) > 1})
    if duplicate_pipelines:
        raise ValueError(f"duplicate pipelines: {', '.join(duplicate_pipelines)}")
    core_pipelines = _core_pipeline_ids()
    core_shadow_pipelines = sorted(set(pipeline_ids) & core_pipelines)
    if core_shadow_pipelines:
        raise ValueError(
            f"plugin pipelines cannot redefine core pipelines: {', '.join(core_shadow_pipelines)}"
        )
    processor_ports = _core_processor_ports()
    processor_ports.update(_plugin_processor_ports(processors))
    processor_attributes = _core_processor_attributes()
    processor_attributes.update(_plugin_processor_attributes(processors))
    for extension in processor_extensions:
        consumer, supplier = processor_ports[extension.processor_id]
        collisions = sorted(set(extension.special_inputs) & set(consumer))
        if collisions:
            raise ValueError(
                f"processor extension {extension.processor_id!r} duplicates "
                f"consumer port(s): {', '.join(collisions)}"
            )
        merged_consumer = dict(consumer)
        merged_consumer.update(extension.special_inputs)
        processor_ports[extension.processor_id] = (merged_consumer, supplier)

        existing_attrs = processor_attributes.setdefault(extension.processor_id, [])
        existing_names = {attr.name for attr in existing_attrs}
        duplicate_attrs = sorted(
            attr.name for attr in extension.special_attributes if attr.name in existing_names
        )
        if duplicate_attrs:
            raise ValueError(
                f"processor extension {extension.processor_id!r} duplicates "
                f"attribute(s): {', '.join(duplicate_attrs)}"
            )
        processor_attributes[extension.processor_id] = [
            *existing_attrs,
            *extension.special_attributes,
        ]
    for pipeline in pipelines:
        unknown_inputs = sorted(set(pipeline.initial_inputs) - known_dts)
        if unknown_inputs:
            raise ValueError(
                f"pipeline {pipeline.pipeline_id!r} references unknown initial "
                f"datatype(s): {', '.join(unknown_inputs)}"
            )
        for step in pipeline.steps:
            if step.processor not in known_processors:
                raise ValueError(
                    f"pipeline {pipeline.pipeline_id!r} step {step.ref!r} "
                    f"references unknown processor {step.processor!r}"
                )
        _validate_pipeline_graph(
            pipeline=pipeline,
            processor_ports=processor_ports,
            processor_attributes=processor_attributes,
            known_datatypes=known_dts,
        )


class PluginBackendCatalog(BaseModel):
    """Combined live declarations exposed by a plugin backend process."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(1, ge=1, le=1)
    plugin_id: str | None = Field(
        default=None,
        max_length=PROVIDER_SELECTOR_COMPONENT_MAX_LENGTH,
        pattern=_CONTRACT_ID_RE.pattern,
    )
    capabilities: list[CapabilityId] = Field(default_factory=list)
    datatypes: list[PluginDataTypeManifest] = Field(default_factory=list)
    processors: list[PluginProcessorManifest] = Field(default_factory=list)
    pipelines: list[PluginPipelineManifest] = Field(default_factory=list)
    processor_extensions: list[PluginProcessorExtensionManifest] = Field(default_factory=list)

    @field_validator("plugin_id")
    @classmethod
    def _plugin_id_format(cls, plugin_id: str | None) -> str | None:
        if plugin_id is not None and not _CONTRACT_ID_RE.match(plugin_id):
            raise ValueError(f"plugin_id must match {_CONTRACT_ID_RE.pattern!r}: {plugin_id!r}")
        if plugin_id is not None and len(plugin_id) > PROVIDER_SELECTOR_COMPONENT_MAX_LENGTH:
            raise ValueError(
                f"plugin_id must be at most {PROVIDER_SELECTOR_COMPONENT_MAX_LENGTH} characters"
            )
        return plugin_id

    @field_validator("capabilities")
    @classmethod
    def _capabilities_are_unique(cls, capabilities: list[str]) -> list[str]:
        return sorted(set(capabilities))

    @model_validator(mode="after")
    def _catalog_is_coherent(self) -> PluginBackendCatalog:
        if self.plugin_id is None and (
            self.datatypes or self.processors or self.pipelines or self.processor_extensions
        ):
            raise ValueError("plugin_id is required when typed declarations are declared")
        if self.plugin_id is not None:
            _validate_plugin_owned_declaration_ids(
                plugin_id=self.plugin_id,
                datatypes=self.datatypes,
                processors=self.processors,
                pipelines=self.pipelines,
            )
        _validate_typed_extension_graph(
            datatypes=self.datatypes,
            processors=self.processors,
            pipelines=self.pipelines,
            processor_extensions=self.processor_extensions,
            declared_capabilities=set(self.capabilities),
            provider_capability_sets=[set(self.capabilities)],
            extension_namespace_prefixes={self.plugin_id} if self.plugin_id else None,
        )
        return self


class LicenseInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    url: str | None = None

    @field_validator("url")
    @classmethod
    def _url_is_public_https(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _validate_public_https_url(value, label="license url")


class UpstreamProject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    url: str
    license: str | None = None

    @field_validator("url")
    @classmethod
    def _url_is_public_https(cls, value: str) -> str:
        return _validate_public_https_url(value, label="upstream project url")


class Compatibility(BaseModel):
    model_config = ConfigDict(extra="allow")

    sfmapi: str = ">=0.0.1"
    python: str | None = ">=3.12,<3.13"
    os: list[str] = Field(default_factory=lambda: ["windows", "linux", "macos"])
    cuda: str | None = None
    torch: TorchRuntime | None = None
    tool_versions: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _extra_values_are_public(self) -> Compatibility:
        for key, value in (self.__pydantic_extra__ or {}).items():
            text_key = str(key)
            if _SENSITIVE_PUBLIC_RE.search(text_key):
                raise ValueError("compatibility extension keys must not contain secrets")
            try:
                values = _public_text_values(value)
            except ValueError:
                raise ValueError(
                    "compatibility extension values must be scalar, list, or object"
                ) from None
            for item in values:
                for variant in _public_text_variants(item):
                    if _SENSITIVE_PUBLIC_RE.search(variant):
                        raise ValueError("compatibility extension values must not contain secrets")
                    if _looks_like_local_path(variant):
                        raise ValueError(
                            "compatibility extension values must not contain local paths"
                        )
                    for url in _URL_RE.findall(variant):
                        if (
                            _public_url_issue(
                                url,
                                allowed_schemes={"http", "https", "git+https"},
                            )
                            is not None
                        ):
                            raise ValueError("compatibility extension URLs must be public")
        return self


class Conformance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["not_run", "partial", "passing", "failing"] = "not_run"
    suite: str | None = None
    report_url: str | None = None
    checked_at: str | None = None

    @field_validator("report_url")
    @classmethod
    def _report_url_is_public_https(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _validate_public_https_url(value, label="conformance report_url")


class PluginDependencyManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plugin_id: str = Field(
        ...,
        max_length=PROVIDER_SELECTOR_COMPONENT_MAX_LENGTH,
        pattern=_CONTRACT_ID_RE.pattern,
        description=(
            "Canonical plugin id reserved for future dependency-aware typed-dataflow "
            "resolution. Current validation remains limited to core and same-plugin "
            "references."
        ),
    )
    version: str | None = Field(
        default=None,
        description=(
            "Optional plugin package/source version constraint reserved for future "
            "dependency-aware typed-dataflow resolution."
        ),
    )

    @field_validator("plugin_id")
    @classmethod
    def _plugin_id_format(cls, plugin_id: str) -> str:
        if not _CONTRACT_ID_RE.match(plugin_id):
            raise ValueError(
                f"plugin dependency id must match {_CONTRACT_ID_RE.pattern!r}: {plugin_id!r}"
            )
        return plugin_id


class PluginManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    plugin_id: str = Field(
        ...,
        max_length=PROVIDER_SELECTOR_COMPONENT_MAX_LENGTH,
        pattern=_CONTRACT_ID_RE.pattern,
    )
    display_name: str
    description: str
    package_name: str
    github_url: str
    entry_points: list[str]
    providers: list[ProviderManifest] = Field(min_length=1)
    runtime_modes: RuntimeModes
    capabilities: list[CapabilityId] = Field(default_factory=list)
    backend_actions: list[str] = Field(default_factory=list)
    config_schemas: list[str] = Field(default_factory=list)
    artifact_contracts: list[str] = Field(default_factory=list)
    datatypes: list[PluginDataTypeManifest] = Field(default_factory=list)
    processors: list[PluginProcessorManifest] = Field(default_factory=list)
    pipelines: list[PluginPipelineManifest] = Field(default_factory=list)
    processor_extensions: list[PluginProcessorExtensionManifest] = Field(default_factory=list)
    plugin_dependencies: list[PluginDependencyManifest] = Field(default_factory=list)
    licenses: list[LicenseInfo] = Field(default_factory=list)
    upstream_projects: list[UpstreamProject] = Field(default_factory=list)
    compatibility: Compatibility = Field(default_factory=Compatibility)
    conformance: Conformance = Field(default_factory=Conformance)
    trust_tier: TrustTier = "community"

    @field_validator("plugin_id")
    @classmethod
    def _plugin_id_format(cls, plugin_id: str) -> str:
        if not _CONTRACT_ID_RE.match(plugin_id):
            raise ValueError(f"plugin_id must match {_CONTRACT_ID_RE.pattern!r}: {plugin_id!r}")
        if len(plugin_id) > PROVIDER_SELECTOR_COMPONENT_MAX_LENGTH:
            raise ValueError(
                f"plugin_id must be at most {PROVIDER_SELECTOR_COMPONENT_MAX_LENGTH} characters"
            )
        return plugin_id

    @field_validator("package_name")
    @classmethod
    def _package_name_is_public(cls, package_name: str) -> str:
        return _validate_public_package_name(package_name, label="package_name")

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

    @field_validator("plugin_dependencies")
    @classmethod
    def _plugin_dependencies_are_unique(
        cls,
        dependencies: list[PluginDependencyManifest],
    ) -> list[PluginDependencyManifest]:
        seen: set[str] = set()
        duplicates: list[str] = []
        for dependency in dependencies:
            if dependency.plugin_id in seen:
                duplicates.append(dependency.plugin_id)
            seen.add(dependency.plugin_id)
        if duplicates:
            raise ValueError(f"duplicate plugin dependencies: {', '.join(sorted(set(duplicates)))}")
        return dependencies

    @field_validator("capabilities")
    @classmethod
    def _capabilities_are_unique(cls, capabilities: list[str]) -> list[str]:
        return sorted(set(capabilities))

    @model_validator(mode="after")
    def _typed_extensions_are_coherent(self) -> PluginManifest:
        declared = set(self.capabilities)
        provider_capability_sets: list[set[str]] = []
        for provider in self.providers:
            declared.update(provider.capabilities)
            provider_capability_sets.append(set(provider.capabilities))
        _validate_plugin_owned_declaration_ids(
            plugin_id=self.plugin_id,
            datatypes=self.datatypes,
            processors=self.processors,
            pipelines=self.pipelines,
        )
        if any(dependency.plugin_id == self.plugin_id for dependency in self.plugin_dependencies):
            raise ValueError("plugin cannot depend on itself")
        _validate_typed_extension_graph(
            datatypes=self.datatypes,
            processors=self.processors,
            pipelines=self.pipelines,
            processor_extensions=self.processor_extensions,
            declared_capabilities=declared,
            provider_capability_sets=provider_capability_sets,
            extension_namespace_prefixes={self.plugin_id},
        )
        return self

    @field_validator("github_url")
    @classmethod
    def _github_url_format(cls, github_url: str) -> str:
        return _validate_github_url(github_url, label="github_url")

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
