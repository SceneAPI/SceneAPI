"""Local plugin installation, enablement, and routing-profile state."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from sfm_hub.install import InstallPlan


class InstalledPlugin(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plugin_id: str
    method: str = "uv"
    source_url: str
    ref: str = "main"
    resolved_commit: str | None = None
    installed_at: str
    enabled: bool = True


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


def record_install(
    plugin_id: str,
    plan: InstallPlan,
    *,
    path: Path | None = None,
    enabled: bool = True,
) -> PluginState:
    state = load_state(path)
    state.installed[plugin_id] = InstalledPlugin(
        plugin_id=plugin_id,
        method=plan.method,
        source_url=plan.source.normalized_url,
        ref=plan.source.ref,
        resolved_commit=plan.resolved_commit,
        installed_at=datetime.now(UTC).isoformat(),
        enabled=enabled,
    )
    save_state(state, path)
    return state


def record_manual_install(
    plugin_id: str,
    *,
    method: str,
    source_url: str = "",
    ref: str = "",
    path: Path | None = None,
    enabled: bool = True,
) -> PluginState:
    state = load_state(path)
    state.installed[plugin_id] = InstalledPlugin(
        plugin_id=plugin_id,
        method=method,
        source_url=source_url,
        ref=ref,
        installed_at=datetime.now(UTC).isoformat(),
        enabled=enabled,
    )
    save_state(state, path)
    return state


def set_enabled(plugin_id: str, enabled: bool, *, path: Path | None = None) -> PluginState:
    state = load_state(path)
    if plugin_id not in state.installed:
        raise KeyError(f"plugin {plugin_id!r} is not installed")
    state.installed[plugin_id].enabled = enabled
    save_state(state, path)
    return state


def upsert_profile(profile: RoutingProfile, *, path: Path | None = None) -> PluginState:
    state = load_state(path)
    state.profiles[profile.name] = profile
    save_state(state, path)
    return state


def set_default_profile(name: str, *, path: Path | None = None) -> PluginState:
    state = load_state(path)
    if name not in state.profiles:
        raise KeyError(f"unknown routing profile {name!r}")
    state.default_profile = name
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
