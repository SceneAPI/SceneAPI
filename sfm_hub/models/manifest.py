"""Plugin manifest envelopes: providers, compatibility metadata, and the manifest itself."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from sfm_hub.models.dataflow import (
    PluginDataTypeManifest,
    PluginPipelineManifest,
    PluginProcessorExtensionManifest,
    PluginProcessorManifest,
    _validate_plugin_owned_declaration_ids,
    _validate_typed_extension_graph,
)
from sfm_hub.models.runtime import RuntimeMode, RuntimeModes, TorchRuntime
from sfm_hub.models.validation import (
    _CONTRACT_ID_RE,
    _ENTRY_POINT_RE,
    _SENSITIVE_PUBLIC_RE,
    _URL_RE,
    PROVIDER_ID_PATTERN,
    PROVIDER_SELECTOR_COMPONENT_MAX_LENGTH,
    CapabilityId,
    _looks_like_local_path,
    _provider_id_re,
    _public_text_values,
    _public_text_variants,
    _public_url_issue,
    _validate_github_url,
    _validate_public_https_url,
    _validate_public_package_name,
)

TrustTier = Literal["official", "verified", "community", "local"]


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
