from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from app.core.errors import ValidationError
from app.services import sfm_stage_service
from sfm_hub.discovery import discover_plugins, load_backend_entry_points
from sfm_hub.doctor import detect_external_tools
from sfm_hub.install import build_docker_install_plan, build_install_plan, parse_github_source
from sfm_hub.models import PluginManifest
from sfm_hub.registry import get_manifest, list_manifests, search_manifests
from sfm_hub.routing import ProviderAmbiguityError, resolve_provider
from sfm_hub.state import (
    RoutingProfile,
    load_state,
    record_manual_install,
    save_state,
    set_project_profile,
    upsert_profile,
)

pytestmark = pytest.mark.unit


def test_bundled_manifests_validate_and_include_initial_entries() -> None:
    manifests = list_manifests()
    plugin_ids = {manifest.plugin_id for manifest in manifests}

    assert {
        "colmap_cli",
        "pycolmap",
        "colmap_native",
        "colmap_legacy",
        "realityscan_cli",
        "hloc",
        "instantsfm",
        "spheresfm",
    } <= plugin_ids

    for manifest in manifests:
        assert manifest.github_url.startswith("https://github.com/SFMAPI/")
        assert manifest.entry_points
        assert manifest.providers
        assert manifest.runtime_mode_names()
        assert set(manifest.provider_ids()) == {
            provider.provider_id for provider in manifest.providers
        }


def test_schema_file_lists_required_manifest_fields() -> None:
    schema = json.loads(Path("sfm_hub/schemas/backend-plugin.schema.json").read_text())

    assert "plugin_id" in schema["required"]
    assert "github_url" in schema["required"]
    assert "entry_points" in schema["required"]
    assert "providers" in schema["required"]
    assert "runtime_modes" in schema["required"]


def test_registry_search_and_github_install_plan() -> None:
    assert [manifest.plugin_id for manifest in search_manifests("hloc")] == ["hloc"]

    source = parse_github_source(
        "https://github.com/SFMAPI/sfmapi_colmap_cli/tree/v1.2.3",
        package="sfmapi-colmap-cli",
    )
    plan = build_install_plan(source)

    assert source.normalized_url == "https://github.com/SFMAPI/sfmapi_colmap_cli.git"
    assert source.ref == "v1.2.3"
    assert plan.command == [
        "uv",
        "pip",
        "install",
        "sfmapi-colmap-cli @ git+https://github.com/SFMAPI/sfmapi_colmap_cli.git@v1.2.3",
    ]
    assert not plan.warnings


def test_mutable_github_refs_warn() -> None:
    plan = build_install_plan(parse_github_source("SFMAPI/sfmapi_hloc"))

    assert plan.source.ref == "main"
    assert plan.warnings


def test_docker_install_plan_reports_missing_image() -> None:
    source = parse_github_source("SFMAPI/sfmapi_colmap_cli", package="sfmapi-colmap-cli")
    plan = build_docker_install_plan(
        "colmap_cli", get_manifest("colmap_cli").runtime_modes.docker, source=source
    )

    assert plan.method == "docker"
    assert plan.warnings


def test_provider_resolution_uses_profiles_and_rejects_ambiguity() -> None:
    record_manual_install("colmap_cli", method="external_tool")
    record_manual_install("pycolmap", method="uv")

    with pytest.raises(ProviderAmbiguityError):
        resolve_provider(stage="features", capability="features.extract.sift")

    assert (
        resolve_provider(
            stage="features",
            capability="features.extract.sift",
            requested_provider="colmap_pycolmap",
        )
        == "colmap_pycolmap"
    )

    upsert_profile(
        RoutingProfile(name="prefer-cli", routes={"features": ["colmap_cli"]}),
    )
    state = load_state()
    state.default_profile = "prefer-cli"
    save_state(state)

    assert resolve_provider(stage="features", capability="features.extract.sift") == "colmap_cli"


def test_provider_resolution_uses_project_profile_before_default() -> None:
    record_manual_install("colmap_cli", method="external_tool")
    record_manual_install("pycolmap", method="uv")
    upsert_profile(RoutingProfile(name="default", routes={"features": ["colmap_cli"]}))
    upsert_profile(RoutingProfile(name="project", routes={"features": ["colmap_pycolmap"]}))
    state = load_state()
    state.default_profile = "default"
    save_state(state)
    set_project_profile("project-1", "project")

    assert (
        resolve_provider(
            stage="features",
            capability="features.extract.sift",
            project_id="project-1",
        )
        == "colmap_pycolmap"
    )


def test_stage_validation_applies_provider_resolution() -> None:
    record_manual_install("colmap_cli", method="external_tool")
    spec = {"type": "sift", "backend_options": {}}

    sfm_stage_service.validate_features_config(spec)

    assert spec["provider"] == "colmap_cli"


def test_stage_validation_reports_ambiguous_provider() -> None:
    record_manual_install("colmap_cli", method="external_tool")
    record_manual_install("pycolmap", method="uv")

    with pytest.raises(ValidationError, match="multiple candidate providers"):
        sfm_stage_service.validate_features_config({"type": "sift", "backend_options": {}})


def test_manifest_lookup_returns_expected_install_metadata() -> None:
    manifest = get_manifest("colmap_cli")

    assert manifest.runtime_modes.uv is not None
    assert manifest.runtime_modes.uv.package == "sfmapi-colmap-cli"
    assert "colmap_cli" in manifest.provider_ids()


def test_external_tool_detection_checks_env_and_version(monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = get_manifest("colmap_cli").model_copy(deep=True)
    assert manifest.runtime_modes.external_tool is not None
    manifest.runtime_modes.external_tool.executable_names = []
    manifest.runtime_modes.external_tool.env_vars = ["TEST_TOOL_EXE"]
    manifest.runtime_modes.external_tool.version_args = ["--version"]
    monkeypatch.setenv("TEST_TOOL_EXE", sys.executable)

    tools = detect_external_tools([manifest])["colmap_cli"]

    assert tools[0].source == "env"
    assert tools[0].path == sys.executable
    assert tools[0].version


def test_entry_point_discovery_loads_plugin_manifest(monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = get_manifest("hloc")

    class FakeEntryPoint:
        name = "hloc"
        value = "fake.module:plugin"
        dist = None

        def load(self) -> PluginManifest:
            return manifest

    class FakeEntryPoints(list[FakeEntryPoint]):
        def select(self, *, group: str) -> list[FakeEntryPoint]:
            assert group == "sfmapi.backends"
            return list(self)

    import sfm_hub.discovery as discovery

    monkeypatch.setattr(
        discovery.metadata, "entry_points", lambda: FakeEntryPoints([FakeEntryPoint()])
    )

    found = discover_plugins(load=True)

    assert found[0].plugin_id == "hloc"
    assert found[0].manifest == manifest


def test_entry_point_loader_registers_backend_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    class PluginObject:
        backend_name = "entry_backend"

        @staticmethod
        def backend_factory() -> object:
            return object()

    class FakeEntryPoint:
        name = "entry_backend"
        value = "fake.module:plugin"
        dist = None

        def load(self) -> PluginObject:
            return PluginObject()

    class FakeEntryPoints(list[FakeEntryPoint]):
        def select(self, *, group: str) -> list[FakeEntryPoint]:
            assert group == "sfmapi.backends"
            return list(self)

    import sfm_hub.discovery as discovery

    monkeypatch.setattr(
        discovery.metadata, "entry_points", lambda: FakeEntryPoints([FakeEntryPoint()])
    )
    registered: dict[str, object] = {}

    def register_backend(name: str, factory: object) -> None:
        registered[name] = factory

    loaded = load_backend_entry_points(register_backend)  # type: ignore[arg-type]

    assert loaded[0].plugin_id == "entry_backend"
    assert "entry_backend" in registered
