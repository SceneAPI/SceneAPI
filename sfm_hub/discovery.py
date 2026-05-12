"""Python entry-point discovery for installed sfmapi backend plugins."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from importlib import metadata
from typing import Any

from sfm_hub.models import PluginManifest

ENTRY_POINT_GROUP = "sfmapi.backends"


@dataclass(frozen=True)
class DiscoveredPlugin:
    """One installed Python entry point in the sfmapi backend group."""

    plugin_id: str
    entry_point: str
    distribution: str | None = None
    version: str | None = None
    manifest: PluginManifest | None = None
    load_error: str | None = None


def _entry_points() -> list[metadata.EntryPoint]:
    eps = metadata.entry_points()
    return list(eps.select(group=ENTRY_POINT_GROUP))


def _dist_name(ep: metadata.EntryPoint) -> str | None:
    dist = getattr(ep, "dist", None)
    if dist is None:
        return None
    metadata_obj = getattr(dist, "metadata", None)
    if metadata_obj is None:
        return None
    name = metadata_obj.get("Name")
    return str(name) if name is not None else None


def _dist_version(ep: metadata.EntryPoint) -> str | None:
    dist = getattr(ep, "dist", None)
    if dist is None:
        return None
    return getattr(dist, "version", None)


def _manifest_from_loaded(obj: Any) -> PluginManifest | None:
    if isinstance(obj, PluginManifest):
        return obj
    if isinstance(obj, dict):
        return PluginManifest.model_validate(obj)
    manifest = getattr(obj, "manifest", None)
    if isinstance(manifest, PluginManifest):
        return manifest
    if isinstance(manifest, dict):
        return PluginManifest.model_validate(manifest)
    get_manifest = getattr(obj, "get_plugin_manifest", None)
    if callable(get_manifest):
        raw = get_manifest()
        if isinstance(raw, PluginManifest):
            return raw
        if isinstance(raw, dict):
            return PluginManifest.model_validate(raw)
    return None


def discover_plugins(*, load: bool = False) -> list[DiscoveredPlugin]:
    """List installed backend plugin entry points.

    `load=False` is safe for fast listing and uses only entry-point
    metadata. `load=True` imports the plugin object and tries to read a
    manifest from it, which is appropriate for operator `doctor` and
    contract checks.
    """

    discovered: list[DiscoveredPlugin] = []
    for ep in sorted(_entry_points(), key=lambda item: item.name):
        manifest: PluginManifest | None = None
        load_error: str | None = None
        if load:
            try:
                manifest = _manifest_from_loaded(ep.load())
            except Exception as exc:  # pragma: no cover - defensive around third-party plugins
                load_error = f"{type(exc).__name__}: {exc}"
        discovered.append(
            DiscoveredPlugin(
                plugin_id=manifest.plugin_id if manifest is not None else ep.name,
                entry_point=ep.value,
                distribution=_dist_name(ep),
                version=_dist_version(ep),
                manifest=manifest,
                load_error=load_error,
            )
        )
    return discovered


def discovered_plugin_ids() -> set[str]:
    return {plugin.plugin_id for plugin in discover_plugins(load=False)}


def discovered_manifests() -> list[PluginManifest]:
    return [
        plugin.manifest
        for plugin in discover_plugins(load=True)
        if plugin.manifest is not None and plugin.load_error is None
    ]


def load_backend_entry_points(
    register_backend: Callable[[str, Callable[[], Any]], None],
) -> list[DiscoveredPlugin]:
    """Load installed backend entry points and register backend factories.

    Supported plugin object shapes:

    - `register(register_backend)` or `register_backend(register_backend)`;
    - `backend_factory` plus optional `backend_name`;
    - a plain callable factory, using the entry-point name as backend id.
    """

    loaded: list[DiscoveredPlugin] = []
    for ep in sorted(_entry_points(), key=lambda item: item.name):
        try:
            obj = ep.load()
            manifest = _manifest_from_loaded(obj)
            register = getattr(obj, "register", None) or getattr(obj, "register_backend", None)
            if callable(register):
                register(register_backend)
            else:
                factory = getattr(obj, "backend_factory", None)
                if factory is None and callable(obj):
                    factory = obj
                if callable(factory):
                    register_backend(str(getattr(obj, "backend_name", ep.name)), factory)
            loaded.append(
                DiscoveredPlugin(
                    plugin_id=manifest.plugin_id if manifest is not None else ep.name,
                    entry_point=ep.value,
                    distribution=_dist_name(ep),
                    version=_dist_version(ep),
                    manifest=manifest,
                )
            )
        except Exception as exc:  # pragma: no cover - defensive around third-party plugins
            loaded.append(
                DiscoveredPlugin(
                    plugin_id=ep.name,
                    entry_point=ep.value,
                    distribution=_dist_name(ep),
                    version=_dist_version(ep),
                    load_error=f"{type(exc).__name__}: {exc}",
                )
            )
    return loaded
