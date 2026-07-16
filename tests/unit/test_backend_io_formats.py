"""Plugin-overridable I/O format resolution at materialization (G2).

A backend may serialize a core DataType its own way (the Format axis is open).
``backend_io_formats`` surfaces those plugin formats and
``backend_default_format_for_kind`` lets the artifact-materialization seam prefer
them -- plugin override, never removal (the core fallback always holds).
"""

from __future__ import annotations

from app.adapters import backend_artifacts as ba
from app.core import artifacts
from app.services import artifact_service


class _ExplicitFormatBackend:
    """Declares its own format object via the optional artifact_formats() hook."""

    name = "explicit"

    def artifact_formats(self) -> list[artifacts.ArtifactFormatDefinition]:
        return [
            artifacts.ArtifactFormatDefinition(
                format_id="explicit.features.custom.v1",
                datatype="feature_set",
                title="custom features",
                description="",
                schema_version=1,
                media_types=("application/octet-stream",),
            )
        ]


class _ContractFormatBackend:
    """Declares a non-core format only via its artifact contracts (derived)."""

    name = "contractor"

    def list_backend_artifact_contracts(self) -> list[dict]:
        return [
            {
                "contract_id": "contractor.features.custom",
                "stage": "features",
                "emits": ["features.local.v1"],
                "emits_formats": ["contractor.features.blob.v1"],
                "preferred": "features.local.v1",
                "preferred_format": "contractor.features.blob.v1",
            }
        ]


class _KindSpecificBackend:
    """Plugin format that serializes ONLY one kind of its DataType."""

    name = "kindspecific"

    def artifact_formats(self) -> list[artifacts.ArtifactFormatDefinition]:
        return [
            artifacts.ArtifactFormatDefinition(
                format_id="ks.features.global.v1",
                datatype="feature_set",
                title="ks global features",
                description="",
                schema_version=1,
                media_types=(),
                serves_kinds=("features.global.v1",),
            )
        ]


class _CoreOnlyBackend:
    """Mirrors the stub/baseline: contracts emit only CORE formats."""

    name = "core"

    def list_backend_artifact_contracts(self) -> list[dict]:
        return [
            {
                "contract_id": "core.features.sift",
                "stage": "features",
                "emits": ["features.local.v1"],
                "emits_formats": ["sfmapi.features.local.v1"],
                "preferred": "features.local.v1",
            }
        ]


def test_explicit_artifact_formats_become_plugin_formats() -> None:
    formats = ba.backend_io_formats(_ExplicitFormatBackend())
    assert [f.format_id for f in formats] == ["explicit.features.custom.v1"]
    # datatype IS the DataType id the format serializes
    assert formats[0].datatype == "feature_set"


def test_derived_plugin_format_from_contracts() -> None:
    formats = ba.backend_io_formats(_ContractFormatBackend())
    assert [f.format_id for f in formats] == ["contractor.features.blob.v1"]
    assert formats[0].datatype == "feature_set"


def test_core_only_backend_yields_no_plugin_formats() -> None:
    # A backend that emits only core formats overrides nothing -- exactly the
    # stub/baseline shape, which is why this whole seam is parity-neutral.
    assert ba.backend_io_formats(_CoreOnlyBackend()) == ()


def test_plugin_overrides_core_default_for_its_datatype() -> None:
    # The kind's DataType is feature_set -> the plugin format wins...
    assert (
        ba.backend_default_format_for_kind("features.local.v1", _ExplicitFormatBackend())
        == "explicit.features.custom.v1"
    )
    # ...but a kind of a *different* DataType keeps the core default (None here).
    assert (
        ba.backend_default_format_for_kind("reconstruction.sparse.v1", _ExplicitFormatBackend())
        is None
    )


def test_kind_specific_override_leaves_siblings_on_core_default() -> None:
    # A plugin format that serves only features.global.v1 overrides THAT kind...
    backend = _KindSpecificBackend()
    assert (
        ba.backend_default_format_for_kind("features.global.v1", backend) == "ks.features.global.v1"
    )
    # ...but the sibling kind of the SAME DataType keeps the core default.
    assert ba.backend_default_format_for_kind("features.local.v1", backend) is None


def test_type_level_override_still_covers_all_kinds() -> None:
    # serves_kinds empty = the whole DataType: both kinds get the override.
    backend = _ExplicitFormatBackend()
    for kind in ("features.local.v1", "features.global.v1"):
        assert ba.backend_default_format_for_kind(kind, backend) == "explicit.features.custom.v1"


def test_no_override_keeps_core_default() -> None:
    assert ba.backend_default_format_for_kind("features.local.v1", _CoreOnlyBackend()) is None
    assert ba.backend_default_format_for_kind("unknown.kind.v1", _ExplicitFormatBackend()) is None


def test_materialization_seam_uses_core_default_without_a_plugin() -> None:
    # The seam defaults the format from the core kind when no backend overrides
    # (backend_io_formats is defensive: no configured backend -> core default).
    descriptor = {"kind": "features.local.v1"}
    artifact_service._validate_artifact_descriptor(descriptor, index=0)
    assert descriptor["artifact_format"] == "sfmapi.features.local.v1"
