"""Stable backend-authoring API for sfmapi plugins."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.adapters.backend import (
    ArtifactConversionBackend,
    Backend,
    BackendIdentity,
    BatchLocalizationBackend,
    ExportBackend,
    FeatureBackend,
    GeometryBackend,
    LocalizationBackend,
    MappingBackend,
    ObservationBackend,
    ProgressReporter,
    ReconstructionMergeBackend,
    ReconstructionReaderBackend,
    RefinementBackend,
    RetrievalBackend,
    RigBackend,
    SfmBackend,
    SphericalBackend,
    TransformBackend,
    UndistortBackend,
    VocabTreeBackend,
    has_backend_method,
    require_backend_method,
)
from app.adapters.backend_actions import (
    BackendActionProvider,
    assert_backend_action_contract,
    backend_action_contract_violations,
    get_backend_action,
    has_backend_actions,
    list_backend_actions,
    run_backend_action,
    validate_backend_action,
)
from app.adapters.backend_artifacts import (
    BackendArtifactContractProvider,
    assert_backend_artifact_contract,
    backend_artifact_contract_violations,
    get_backend_artifact_contract,
    has_backend_artifact_contracts,
    list_backend_artifact_contracts,
)
from app.adapters.backend_config import (
    BackendConfigSchemaProvider,
    assert_backend_config_contract,
    backend_config_contract_violations,
    get_backend_config_schema,
    has_backend_config_schemas,
    list_backend_config_schemas,
    validate_backend_options,
)
from app.adapters.backend_contract import (
    assert_backend_contract,
    backend_capability_contract_violations,
    backend_contract_violations,
)
from app.adapters.progress import (
    LogLevel,
    NoopProgressReporter,
    accepts_progress,
    call_with_optional_progress,
)
from app.adapters.registry import (
    get_backend,
    list_backend_providers,
    list_backends,
    register_backend,
    register_backend_provider,
)


@dataclass(frozen=True)
class Plugin:
    """Canonical sfmapi plugin shape.

    Every ``[sfmapi.backends]`` entry point in the baseline has
    converged on this exact structure: a manifest dict, a backend name
    in the sfmapi registry, a zero-arg factory that builds the
    backend, and the ``register()`` method that wires the factory in
    via :func:`app.adapters.registry.register_backend`. Plugin authors
    typically instantiated their own dataclass per repo; importing
    :class:`Plugin` removes that boilerplate.

    Usage::

        from sfmapi.backends import Plugin
        from .backend import MyBackend

        MANIFEST = {...}  # PluginManifestDict-shaped dict

        plugin = Plugin(
            manifest=MANIFEST,
            backend_name="my_backend",
            backend_factory=MyBackend,
        )

    The entry point in ``pyproject.toml`` is then::

        [project.entry-points."sfmapi.backends"]
        my_backend = "sfmapi_my_backend.plugin:plugin"

    :attr:`register` is forward-compatible with older sfmapi versions
    that do not accept a ``providers=`` keyword on the registrar.
    """

    manifest: dict[str, Any]
    backend_name: str
    backend_factory: Callable[[], Any]

    def get_plugin_manifest(self) -> dict[str, Any]:
        return self.manifest

    def register(self, register_backend: Callable[..., None]) -> None:
        provider_ids = [
            str(provider["provider_id"])
            for provider in self.manifest.get("providers", [])
        ]
        try:
            register_backend(
                self.backend_name,
                self.backend_factory,
                providers=provider_ids,
            )
        except TypeError:
            # Older sfmapi without ``providers=`` kwarg on the registrar.
            register_backend(self.backend_name, self.backend_factory)


__all__ = [
    "ArtifactConversionBackend",
    "Backend",
    "BackendActionProvider",
    "BackendArtifactContractProvider",
    "BackendConfigSchemaProvider",
    "BackendIdentity",
    "BatchLocalizationBackend",
    "ExportBackend",
    "FeatureBackend",
    "GeometryBackend",
    "LocalizationBackend",
    "LogLevel",
    "MappingBackend",
    "NoopProgressReporter",
    "ObservationBackend",
    "Plugin",
    "ProgressReporter",
    "ReconstructionMergeBackend",
    "ReconstructionReaderBackend",
    "RefinementBackend",
    "RetrievalBackend",
    "RigBackend",
    "SfmBackend",
    "SphericalBackend",
    "TransformBackend",
    "UndistortBackend",
    "VocabTreeBackend",
    "accepts_progress",
    "assert_backend_action_contract",
    "assert_backend_artifact_contract",
    "assert_backend_config_contract",
    "assert_backend_contract",
    "backend_action_contract_violations",
    "backend_artifact_contract_violations",
    "backend_capability_contract_violations",
    "backend_config_contract_violations",
    "backend_contract_violations",
    "call_with_optional_progress",
    "get_backend",
    "get_backend_action",
    "get_backend_artifact_contract",
    "get_backend_config_schema",
    "has_backend_actions",
    "has_backend_artifact_contracts",
    "has_backend_config_schemas",
    "has_backend_method",
    "list_backend_actions",
    "list_backend_artifact_contracts",
    "list_backend_config_schemas",
    "list_backend_providers",
    "list_backends",
    "register_backend",
    "register_backend_provider",
    "require_backend_method",
    "run_backend_action",
    "validate_backend_action",
    "validate_backend_options",
]
