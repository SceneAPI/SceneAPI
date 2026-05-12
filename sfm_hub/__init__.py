"""Plugin registry helpers used by sfmapi's CLI and operator API."""

from __future__ import annotations

from sfm_hub.install import GitHubSource, InstallPlan, build_install_plan, parse_github_source
from sfm_hub.models import PluginManifest, ProviderManifest
from sfm_hub.registry import get_manifest, list_manifests, search_manifests

__all__ = [
    "GitHubSource",
    "InstallPlan",
    "PluginManifest",
    "ProviderManifest",
    "build_install_plan",
    "get_manifest",
    "list_manifests",
    "parse_github_source",
    "search_manifests",
]
