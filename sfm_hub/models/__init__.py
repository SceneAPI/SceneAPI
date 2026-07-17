"""Typed plugin manifest models for sfm_hub.

Facade package: the models are implemented in cohesive submodules
(:mod:`~sfm_hub.models.validation`, :mod:`~sfm_hub.models.runtime`,
:mod:`~sfm_hub.models.dataflow`, :mod:`~sfm_hub.models.manifest`) and
re-exported here so ``sfm_hub.models`` keeps its complete historical
surface — import from this package, not from the submodules.
"""

from __future__ import annotations

# Private helpers and vocabularies re-exported for intra-repo consumers
# (e.g. sfm_hub.doctor and sceneapi.server.workers.tasks.radiance_train use
# _public_url_issue) and for test back-compat with the pre-split module.
from sfm_hub.models.dataflow import (
    PluginAttributeManifest,
    PluginDataTypeManifest,
    PluginPipelineManifest,
    PluginPipelineStepManifest,
    PluginPortSpecManifest,
    PluginProcessorExtensionManifest,
    PluginProcessorManifest,
    PluginSpecialAttributeManifest,
    PluginSpecialInputPortSpecManifest,
    WireList,
)
from sfm_hub.models.dataflow import (
    _core_processor_attributes as _core_processor_attributes,
)
from sfm_hub.models.dataflow import (
    _core_processor_ports as _core_processor_ports,
)
from sfm_hub.models.dataflow import (
    _parse_wire_ref as _parse_wire_ref,
)
from sfm_hub.models.dataflow import (
    _plugin_processor_attributes as _plugin_processor_attributes,
)
from sfm_hub.models.dataflow import (
    _plugin_processor_ports as _plugin_processor_ports,
)
from sfm_hub.models.dataflow import (
    _requires_verified_match_graph as _requires_verified_match_graph,
)
from sfm_hub.models.dataflow import (
    _validate_attribute_schema as _validate_attribute_schema,
)
from sfm_hub.models.dataflow import (
    _validate_pipeline_graph as _validate_pipeline_graph,
)
from sfm_hub.models.dataflow import (
    _validate_plugin_owned_declaration_ids as _validate_plugin_owned_declaration_ids,
)
from sfm_hub.models.dataflow import (
    _validate_step_attributes as _validate_step_attributes,
)
from sfm_hub.models.dataflow import (
    _validate_typed_extension_graph as _validate_typed_extension_graph,
)
from sfm_hub.models.dataflow import (
    _value_matches_attribute as _value_matches_attribute,
)
from sfm_hub.models.dataflow import (
    _wire_values as _wire_values,
)
from sfm_hub.models.manifest import (
    Compatibility,
    Conformance,
    LicenseInfo,
    PluginBackendCatalog,
    PluginDependencyManifest,
    PluginManifest,
    ProviderManifest,
    TrustTier,
    UpstreamProject,
)
from sfm_hub.models.runtime import (
    ContainerServiceBuild,
    ContainerServiceCache,
    ContainerServiceEndpoint,
    ContainerServiceExecution,
    ContainerServiceHealthcheck,
    ContainerServiceImage,
    ContainerServiceMounts,
    ContainerServiceObjectStore,
    ContainerServiceProvenance,
    ContainerServiceRetry,
    ContainerServiceRuntime,
    DockerRuntime,
    ExternalToolRuntime,
    RuntimeMode,
    RuntimeModes,
    TorchRuntime,
    UvRuntime,
)
from sfm_hub.models.validation import (
    _ATTRIBUTE_RE as _ATTRIBUTE_RE,
)
from sfm_hub.models.validation import (
    _CONTRACT_ID_RE as _CONTRACT_ID_RE,
)
from sfm_hub.models.validation import (
    _ENTRY_POINT_RE as _ENTRY_POINT_RE,
)
from sfm_hub.models.validation import (
    _ENV_VAR_RE as _ENV_VAR_RE,
)
from sfm_hub.models.validation import (
    _GITHUB_NAME_RE as _GITHUB_NAME_RE,
)
from sfm_hub.models.validation import (
    _LOCAL_DECLARATION_ID_RE as _LOCAL_DECLARATION_ID_RE,
)
from sfm_hub.models.validation import (
    _PUBLIC_IMAGE_REF_RE as _PUBLIC_IMAGE_REF_RE,
)
from sfm_hub.models.validation import (
    _PUBLIC_PACKAGE_RE as _PUBLIC_PACKAGE_RE,
)
from sfm_hub.models.validation import (
    _PUBLIC_REF_RE as _PUBLIC_REF_RE,
)
from sfm_hub.models.validation import (
    _RESOLVER_ENV_KEYS as _RESOLVER_ENV_KEYS,
)
from sfm_hub.models.validation import (
    _ROLE_RE as _ROLE_RE,
)
from sfm_hub.models.validation import (
    _SENSITIVE_PUBLIC_RE as _SENSITIVE_PUBLIC_RE,
)
from sfm_hub.models.validation import (
    _SPECIAL_ROLE_RE as _SPECIAL_ROLE_RE,
)
from sfm_hub.models.validation import (
    _URL_RE as _URL_RE,
)
from sfm_hub.models.validation import (
    PROVIDER_ID_PATTERN,
    PROVIDER_SELECTOR_COMPONENT_MAX_LENGTH,
    CapabilityId,
)
from sfm_hub.models.validation import (
    _core_datatype_ids as _core_datatype_ids,
)
from sfm_hub.models.validation import (
    _core_pipeline_ids as _core_pipeline_ids,
)
from sfm_hub.models.validation import (
    _core_processor_ids as _core_processor_ids,
)
from sfm_hub.models.validation import (
    _decoded_path_params as _decoded_path_params,
)
from sfm_hub.models.validation import (
    _deny_core_ids_schema as _deny_core_ids_schema,
)
from sfm_hub.models.validation import (
    _known_capabilities as _known_capabilities,
)
from sfm_hub.models.validation import (
    _looks_like_local_path as _looks_like_local_path,
)
from sfm_hub.models.validation import (
    _private_registry_host as _private_registry_host,
)
from sfm_hub.models.validation import (
    _provider_id_re as _provider_id_re,
)
from sfm_hub.models.validation import (
    _public_text_values as _public_text_values,
)
from sfm_hub.models.validation import (
    _public_text_variants as _public_text_variants,
)
from sfm_hub.models.validation import (
    _public_url_issue as _public_url_issue,
)
from sfm_hub.models.validation import (
    _validate_github_url as _validate_github_url,
)
from sfm_hub.models.validation import (
    _validate_public_build_args as _validate_public_build_args,
)
from sfm_hub.models.validation import (
    _validate_public_env_mapping as _validate_public_env_mapping,
)
from sfm_hub.models.validation import (
    _validate_public_https_url as _validate_public_https_url,
)
from sfm_hub.models.validation import (
    _validate_public_image_ref as _validate_public_image_ref,
)
from sfm_hub.models.validation import (
    _validate_public_package_name as _validate_public_package_name,
)
from sfm_hub.models.validation import (
    _validate_public_ref as _validate_public_ref,
)
from sfm_hub.models.validation import (
    _validate_public_relative_path as _validate_public_relative_path,
)
from sfm_hub.models.validation import (
    _validate_public_service_path as _validate_public_service_path,
)

__all__ = [
    "PROVIDER_ID_PATTERN",
    "PROVIDER_SELECTOR_COMPONENT_MAX_LENGTH",
    "CapabilityId",
    "Compatibility",
    "Conformance",
    "ContainerServiceBuild",
    "ContainerServiceCache",
    "ContainerServiceEndpoint",
    "ContainerServiceExecution",
    "ContainerServiceHealthcheck",
    "ContainerServiceImage",
    "ContainerServiceMounts",
    "ContainerServiceObjectStore",
    "ContainerServiceProvenance",
    "ContainerServiceRetry",
    "ContainerServiceRuntime",
    "DockerRuntime",
    "ExternalToolRuntime",
    "LicenseInfo",
    "PluginAttributeManifest",
    "PluginBackendCatalog",
    "PluginDataTypeManifest",
    "PluginDependencyManifest",
    "PluginManifest",
    "PluginPipelineManifest",
    "PluginPipelineStepManifest",
    "PluginPortSpecManifest",
    "PluginProcessorExtensionManifest",
    "PluginProcessorManifest",
    "PluginSpecialAttributeManifest",
    "PluginSpecialInputPortSpecManifest",
    "ProviderManifest",
    "RuntimeMode",
    "RuntimeModes",
    "TorchRuntime",
    "TrustTier",
    "UpstreamProject",
    "UvRuntime",
    "WireList",
]
