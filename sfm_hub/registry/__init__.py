"""Bundled backend plugin registry."""

from __future__ import annotations

from importlib import resources
from importlib.resources.abc import Traversable

from sfm_hub.discovery import discovered_manifests
from sfm_hub.models import PluginManifest


def _backend_root() -> Traversable:
    return resources.files("sfm_hub.registry.backends")


def list_manifests(*, include_entry_points: bool = True) -> list[PluginManifest]:
    manifests: list[PluginManifest] = []
    for child in _backend_root().iterdir():
        manifest_file = child / "manifest.json"
        if manifest_file.is_file():
            manifests.append(PluginManifest.model_validate_json(manifest_file.read_text()))
    if include_entry_points:
        by_id = {manifest.plugin_id: manifest for manifest in manifests}
        for manifest in discovered_manifests():
            by_id.setdefault(manifest.plugin_id, manifest)
        manifests = list(by_id.values())
    return sorted(manifests, key=lambda item: item.plugin_id)


def get_manifest(plugin_id: str) -> PluginManifest:
    for manifest in list_manifests():
        if manifest.plugin_id == plugin_id:
            return manifest
    raise KeyError(f"unknown sfm_hub plugin {plugin_id!r}")


def search_manifests(query: str) -> list[PluginManifest]:
    needle = query.casefold()
    return [
        manifest
        for manifest in list_manifests()
        if needle in manifest.plugin_id.casefold()
        or needle in manifest.display_name.casefold()
        or needle in manifest.description.casefold()
        or any(needle in provider.provider_id.casefold() for provider in manifest.providers)
    ]
