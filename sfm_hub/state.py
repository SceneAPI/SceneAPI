"""Local plugin installation, enablement, and routing-profile state."""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from sfm_hub.install import InstallPlan

_log = logging.getLogger(__name__)

ProvisioningStatus = Literal["not_requested", "planned", "running", "succeeded", "failed"]


class InstalledPlugin(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plugin_id: str
    method: str = "uv"
    source_url: str
    ref: str = "main"
    resolved_commit: str | None = None
    installed_at: str
    enabled: bool = True
    provision_runtime: bool = False
    provisioned: bool = False
    provisioning_status: ProvisioningStatus = "not_requested"
    provisioning_error: str | None = None
    request_id: str | None = None


class RoutingProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    routes: dict[str, list[str]] = Field(default_factory=dict)


class PluginState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    installed: dict[str, InstalledPlugin] = Field(default_factory=dict)
    provider_priority: list[str] = Field(default_factory=list)
    profiles: dict[str, RoutingProfile] = Field(default_factory=dict)
    default_profile: str | None = None
    project_profiles: dict[str, str] = Field(default_factory=dict)
    workspace_profiles: dict[str, str] = Field(default_factory=dict)


def default_state_path() -> Path:
    override = os.environ.get("SFMAPI_PLUGIN_STATE")
    if override:
        return Path(override)
    return Path.home() / ".config" / "sfmapi" / "plugins.json"


def load_state(path: Path | None = None) -> PluginState:
    state_path = path or default_state_path()
    if not state_path.exists():
        return PluginState()
    return PluginState.model_validate_json(state_path.read_text(encoding="utf-8"))


def save_state(state: PluginState, path: Path | None = None) -> None:
    state_path = path or default_state_path()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(state.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _record_installed(
    state: PluginState,
    record: InstalledPlugin,
    *,
    path: Path | None,
) -> PluginState:
    """Persist an install record, idempotent-by-ref.

    Re-recording the same plugin with an identical method / source / ref /
    enabled state is a no-op (no rewritten ``installed_at``). A record that
    differs is treated as a reinstall: it overwrites the prior one but logs
    a warning first, so a method/ref change isn't silent.
    """
    plugin_id = record.plugin_id
    existing = state.installed.get(plugin_id)
    if existing is not None:
        same = (
            existing.method == record.method
            and existing.source_url == record.source_url
            and existing.ref == record.ref
            and existing.enabled == record.enabled
            and existing.provision_runtime == record.provision_runtime
            and existing.provisioned == record.provisioned
            and existing.provisioning_status == record.provisioning_status
            and existing.provisioning_error == record.provisioning_error
            and existing.request_id == record.request_id
        )
        if same:
            return state
        _log.warning(
            "sfm_hub.state.reinstall_overwrites_record",
            extra={
                "plugin_id": plugin_id,
                "old": f"{existing.method}@{existing.ref}",
                "new": f"{record.method}@{record.ref}",
            },
        )
    state.installed[plugin_id] = record
    save_state(state, path)
    return state


def record_install(
    plugin_id: str,
    plan: InstallPlan,
    *,
    path: Path | None = None,
    enabled: bool = True,
    provision_runtime: bool = False,
    provisioned: bool = False,
    provisioning_status: ProvisioningStatus = "not_requested",
    provisioning_error: str | None = None,
    request_id: str | None = None,
) -> PluginState:
    state = load_state(path)
    return _record_installed(
        state,
        InstalledPlugin(
            plugin_id=plugin_id,
            method=plan.method,
            source_url=plan.source.normalized_url,
            ref=plan.source.ref,
            resolved_commit=plan.resolved_commit,
            installed_at=datetime.now(UTC).isoformat(),
            enabled=enabled,
            provision_runtime=provision_runtime,
            provisioned=provisioned,
            provisioning_status=provisioning_status,
            provisioning_error=provisioning_error,
            request_id=request_id,
        ),
        path=path,
    )


def record_manual_install(
    plugin_id: str,
    *,
    method: str,
    source_url: str = "",
    ref: str = "",
    path: Path | None = None,
    enabled: bool = True,
    provision_runtime: bool = False,
    provisioned: bool = False,
    provisioning_status: ProvisioningStatus = "not_requested",
    provisioning_error: str | None = None,
    request_id: str | None = None,
) -> PluginState:
    state = load_state(path)
    return _record_installed(
        state,
        InstalledPlugin(
            plugin_id=plugin_id,
            method=method,
            source_url=source_url,
            ref=ref,
            installed_at=datetime.now(UTC).isoformat(),
            enabled=enabled,
            provision_runtime=provision_runtime,
            provisioned=provisioned,
            provisioning_status=provisioning_status,
            provisioning_error=provisioning_error,
            request_id=request_id,
        ),
        path=path,
    )


def set_enabled(plugin_id: str, enabled: bool, *, path: Path | None = None) -> PluginState:
    state = load_state(path)
    if plugin_id not in state.installed:
        raise KeyError(f"plugin {plugin_id!r} is not installed")
    state.installed[plugin_id].enabled = enabled
    save_state(state, path)
    return state


def upsert_profile(profile: RoutingProfile, *, path: Path | None = None) -> PluginState:
    """Create or replace a routing profile.

    Every provider id in ``profile.routes`` must be declared by some known
    plugin manifest (bundled or discovered) — otherwise a typo'd id
    silently no-ops at routing time. Validating against the full manifest
    universe (not just *installed* plugins) still lets operators stage a
    routing profile before installing the plugin.
    """
    known = _known_provider_ids()
    unknown = sorted({pid for providers in profile.routes.values() for pid in providers} - known)
    if unknown:
        raise KeyError(
            f"routing profile {profile.name!r} references unknown provider id(s): "
            f"{', '.join(unknown)}"
        )
    state = load_state(path)
    state.profiles[profile.name] = profile
    save_state(state, path)
    return state


def _known_provider_ids() -> set[str]:
    """Provider ids declared by any bundled or discovered plugin manifest.

    Late import: ``sfm_hub.registry`` -> ``sfm_hub.discovery`` -> this
    module, so importing it at module load would cycle.
    """
    from sfm_hub.registry import list_manifests

    return {provider_id for manifest in list_manifests() for provider_id in manifest.provider_ids()}


def set_default_profile(name: str, *, path: Path | None = None) -> PluginState:
    state = load_state(path)
    if name not in state.profiles:
        raise KeyError(f"unknown routing profile {name!r}")
    state.default_profile = name
    save_state(state, path)
    return state


def set_provider_priority(providers: list[str], *, path: Path | None = None) -> PluginState:
    known = _known_provider_ids()
    unknown = sorted(set(providers) - known)
    if unknown:
        raise KeyError(
            "provider priority references unknown provider id(s): "
            f"{', '.join(unknown)}"
        )
    state = load_state(path)
    state.provider_priority = list(providers)
    save_state(state, path)
    return state


def set_project_profile(
    project_id: str,
    profile_name: str,
    *,
    path: Path | None = None,
) -> PluginState:
    state = load_state(path)
    if profile_name not in state.profiles:
        raise KeyError(f"unknown routing profile {profile_name!r}")
    state.project_profiles[project_id] = profile_name
    save_state(state, path)
    return state


def set_workspace_profile(
    workspace: str,
    profile_name: str,
    *,
    path: Path | None = None,
) -> PluginState:
    state = load_state(path)
    if profile_name not in state.profiles:
        raise KeyError(f"unknown routing profile {profile_name!r}")
    state.workspace_profiles[workspace] = profile_name
    save_state(state, path)
    return state
